# -*- coding: utf-8 -*-
# Vendored from DexSubtitles 5.7.3 (service.subtitles.dexworld, AUTOSYNC_V573) for
# Dex Hub 5.10.117: Dex Hub runs this AutoSync itself only while DexSubtitles
# is not enabled (subsync/runner.py), so the two never sync the same subtitle.
"""DexSubtitles AutoSync (AUTOSYNC_V570 + V572 + V573): المزامنة على نفس الجهاز.

الترجمة الأصلية تشتغل فوراً. خدمة الخلفية تقرأ فهرس Cues من نفس ملف التشغيل
(غالباً أقل من 2 ميغا بطلبين أو ثلاثة، بدون ما تقرأ الفيديو)، وتاخذ توقيت الترجمة
النصية المدمجة مرجعاً، وتطابق عليه توقيت الترجمة العربية، وتبدلها بس إذا كان
التطابق عالي وواضح. غير كذا تبقى الترجمة الأصلية كما هي.
ما فيه سيرفر ولا مفاتيح، وكل الشغل في خيط خلفي ما يوقف التشغيل.
"""

import os
import io
import json
import time
import hashlib
import threading
from urllib.parse import urlsplit, parse_qsl, unquote_plus

import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
try:
    import requests
except Exception:
    from . import minireq as requests
try:
    import urllib3
    urllib3.disable_warnings()
except Exception:
    pass

from . import dexcues
from . import dexalign

ADDON = xbmcaddon.Addon('plugin.video.dexhub')
BASE_TEMP = xbmcvfs.translatePath("special://temp/")
TEMP_DIR = os.path.join(BASE_TEMP, "dexworld_subs")
QUEUE_DIR = os.path.join(TEMP_DIR, "autosync_queue")
CACHE_DIR = os.path.join(TEMP_DIR, "autosync_cache")
WATCH_DIR = os.path.join(TEMP_DIR, "autosync_watch")   # AUTOSYNC_V573: نسخ الترجمات اللي لقاها مراقب الترجمات
for _d in (TEMP_DIR, QUEUE_DIR, CACHE_DIR, WATCH_DIR):
    try:
        if not xbmcvfs.exists(_d):
            xbmcvfs.mkdirs(_d)
    except Exception:
        pass

_MAX_SUB_BYTES = 8 * 1024 * 1024
_MIN_JOB_AGE = 2          # ثواني: نخلي Kodi يحمّل الترجمة الأصلية قبل ما نبدّلها
_JOB_TTL = 180
_REF_OK_TTL = 30 * 60
_REF_FAIL_TTL = 10 * 60
_TRANSIENT = ('timeout', 'http_io', 'io_error', 'budget_requests', 'budget_bytes')
_WORK_LOCK = threading.Lock()
_RUNNING = set()
_REF_LOCK = threading.Lock()
_REF_CACHE = {}
_LAST_CLEAN = [0]
_SESSION = requests.Session()
_SESSION.headers.update({'User-Agent': 'DexHub-AutoSync/2 (Kodi)'})
_SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt')
_VFS_PREFIXES = ('smb://', 'nfs://', 'special://', 'dav://', 'davs://', 'ftp://', 'sftp://', 'upnp://', 'file://')


def _t(text):
    """Dex Hub's language (the messages are DexSubtitles' Arabic)."""
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] autosync: %s' % msg, level)


def _setting_bool(key, default=False):
    try:
        raw = (ADDON.getSetting(key) or '').strip().lower()
        if raw in ('1', 'true', 'yes', 'on'):
            return True
        if raw in ('0', 'false', 'no', 'off'):
            return False
    except Exception:
        pass
    return default


def _setting_int(key, default, lo=None, hi=None):
    try:
        value = int(float(ADDON.getSetting(key) or default))
    except Exception:
        value = int(default)
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


# AUTOSYNC_V572: كل اختيار يطلع له نتيجة على الشاشة، وآخر اختيار هو اللي يثبت.
_REASONS = {
    'not_mkv': 'الملف مو MKV، غالباً MP4، فما فيه فهرس نأخذ منه التوقيت',
    'no_cues': 'فهرس الملف ناقص', 'no_segment': 'فهرس الملف ناقص', 'unknown_size': 'فهرس الملف ناقص',
    'truncated': 'فهرس الملف ناقص', 'short_file': 'فهرس الملف ناقص', 'no_tracks': 'فهرس الملف ناقص',
    'no_subtitle_tracks': 'الملف ما فيه ترجمة مدمجة نأخذ منها التوقيت',
    'no_usable_subtitles': 'الترجمات المدمجة في الملف ما تصلح مرجعاً',
    'no_usable_text_track': 'الترجمات المدمجة في الملف قليلة أو ما تصلح مرجعاً',
    'no_range': 'الخادم ما يسمح بقراءة جزء من الملف',
    'cues_too_large': 'فهرس الملف كبير جداً', 'element_too_large': 'فهرس الملف كبير جداً',
}


def _reason_text(reason):
    reason = str(reason or '')
    if reason in _REASONS:
        return _t(_REASONS[reason])
    if reason in _TRANSIENT or reason.startswith('http_') or reason == 'error':
        return _t('ما قدرت أقرأ فهرس الملف الحين، بحاول مع الاختيار الجاي')
    return _t('ما لقيت مرجع توقيت في الملف')


def _notify(msg, ok):
    """ok: نجحت المزامنة. غير كذا تنبيه بالسبب، وله إعداد لحاله."""
    if not _setting_bool('autosync_notify' if ok else 'autosync_notify_skip', True):
        return
    try:
        xbmcgui.Dialog().notification('Dex Hub AutoSync', msg,
                                      xbmcgui.NOTIFICATION_INFO if ok else xbmcgui.NOTIFICATION_WARNING, 3500)
    except Exception:
        pass


def _latest_path(media_url):
    tag = hashlib.sha1((media_url or '').encode('utf-8', 'ignore')).hexdigest()[:16]
    return os.path.join(QUEUE_DIR, 'latest_%s.txt' % tag)


def _mark_latest(media_url, key):
    try:
        _write_bytes(_latest_path(media_url), key.encode('ascii'))
    except Exception:
        pass


def _is_latest(job):
    try:
        with io.open(_latest_path(job.get('media_url') or ''), 'r', encoding='ascii') as f:
            return f.read().strip() == (job.get('key') or '')
    except Exception:
        return True


def _safe_host(url):
    try:
        u = urlsplit(url or '')
        return '%s://%s' % (u.scheme, u.hostname or '') if u.scheme else '(local)'
    except Exception:
        return ''


def _split_kodi_media_url(url):
    """يفصل صيغة Kodi: رابط|Header=Value بدون ما يسجّل أي توكن."""
    raw = str(url or '')
    if '|' not in raw:
        return raw, {}
    base, tail = raw.split('|', 1)
    headers = {}
    try:
        for k, v in parse_qsl(tail, keep_blank_values=True):
            k = str(k or '').strip()
            if not k or len(k) > 80 or len(v) > 4096:
                continue
            headers[k] = unquote_plus(v)
    except Exception:
        headers = {}
    return base, headers


def _media_kind(url):
    """http للروابط، vfs للملفات المحلية والشبكة، وفاضي لكل شي ما نقدر نقرأ فهرسه."""
    base, _h = _split_kodi_media_url(url)
    low = (base or '').strip().lower()
    if not low:
        return ''
    path = low.split('?', 1)[0]
    if path.endswith(('.m3u8', '.mpd', '.iso', '.strm')) or '/master.m3u8' in low:
        return ''
    if low.startswith(('http://', 'https://')):
        return 'http'
    if low.startswith(_VFS_PREFIXES) or low.startswith('/') or (len(low) > 2 and low[1] == ':' and low[2] in '\\/'):
        return 'vfs'
    return ''  # plugin:// pvr:// stack:// rtmp:// udp:// ...


def _current_media_url(player=None):
    try:
        p = player or xbmc.Player()
        return p.getPlayingFile() or ''
    except Exception:
        return ''


def _read_bytes(path):
    with io.open(path, 'rb') as f:
        return f.read(_MAX_SUB_BYTES + 1)


def _write_bytes(path, data):
    tmp = path + '.tmp'
    with io.open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _sub_ext(path):
    ext = os.path.splitext(path or '')[1].lower()
    return ext if ext in _SUB_EXTS else '.srt'


def _job_key(sub_hash, media_url):
    return hashlib.sha1(('%s\n%s' % (sub_hash, media_url or '')).encode('utf-8', 'ignore')).hexdigest()


def enqueue(subtitle_path, media_url=None, display_name='', source_url='', provider='', is_ai=False,
            origin='dexsubs', quiet=False, expect=None, source=None):
    """يضيف مهمة مزامنة للخدمة الخلفية. ما يوقف التشغيل أبداً.
    origin (AUTOSYNC_V573): dexsubs اختيار من DexSubtitles، watch اختيار من إضافة ثانية،
    startup ترجمة شغّلها كودي لحاله (جنب الفيديو أو من الإضافة) وتنبيهها بس لو انضبطت.
    expect: الترجمة الشغالة وقت الطلب؛ لو تغيرت أو انطفت الترجمة قبل التطبيق ما نطبق.
    source: الملف اللي كودي شغّله {'path','stat'}؛ لو انكتب فوقه (اختيار جديد بنفس الاسم) ما نطبق."""
    if not _setting_bool('autosync_enabled', True):
        return False
    if is_ai and not _setting_bool('autosync_include_ai', False):
        if not quiet:
            _notify(_t('ترجمة AI: المزامنة مطفية لها من الإعدادات'), False)
        return False
    subtitle_path = xbmcvfs.translatePath(subtitle_path or '')
    if not subtitle_path or not os.path.exists(subtitle_path):
        return False
    media_url = media_url or _current_media_url()
    if not _media_kind(media_url):
        _log('skip: playback source has no readable MKV index (%s)' % _safe_host(_split_kodi_media_url(media_url)[0]))
        if media_url and not quiet:
            _notify(_t('المصدر بث مباشر أو رابط ما ينقرى فهرسه، فما فيه مزامنة'), False)
        return False
    try:
        raw = _read_bytes(subtitle_path)
    except Exception:
        return False
    if not raw or len(raw) > _MAX_SUB_BYTES:
        _log('skip: subtitle empty or too large', xbmc.LOGWARNING)
        return False
    sub_hash = hashlib.sha1(raw).hexdigest()
    key = _job_key(sub_hash, media_url)
    job_path = os.path.join(QUEUE_DIR, '%s.json' % key)
    _mark_latest(media_url, key)  # AUTOSYNC_V572: آخر اختيار يغلب أي مهمة أقدم
    if os.path.exists(job_path):
        return True
    job = {
        'v': 2,
        'key': key,
        'created_at': time.time(),
        'subtitle_path': subtitle_path,
        'sub_hash': sub_hash,
        'media_url': media_url,
        'display_name': str(display_name or ''),
        'provider': str(provider or ''),
        'origin': str(origin or 'dexsubs'),
        'expect': expect if isinstance(expect, dict) else None,
        'source': source if isinstance(source, dict) else None,
    }
    try:
        with io.open(job_path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(job, f, ensure_ascii=False)
        os.replace(job_path + '.tmp', job_path)
        _log('queued %s (%s)' % (key[:8], _safe_host(_split_kodi_media_url(media_url)[0])))
        return True
    except Exception as e:
        _log('queue failed: %s' % e, xbmc.LOGWARNING)
        try:
            os.remove(job_path + '.tmp')
        except Exception:
            pass
        return False


def _still_same_playback(player, job):
    try:
        if not player.isPlayingVideo():
            return False
        cur = player.getPlayingFile() or ''
        return bool(cur and cur == (job.get('media_url') or ''))
    except Exception:
        return False


def _vfs_open(path):
    return xbmcvfs.File(path)


def _reference(media_url):
    """جدول توقيت المرجع من فهرس الملف، مع ذاكرة لنفس الفيديو (تبديل الترجمات ما يعيد القراءة)."""
    now = time.time()
    with _REF_LOCK:
        hit = _REF_CACHE.get(media_url)
        if hit and now < hit[0]:
            return hit[1], True
    kind = _media_kind(media_url)
    base, headers = _split_kodi_media_url(media_url)
    budget = dexcues.Budget(dexcues.DEFAULTS)
    try:
        if kind == 'http':
            src = dexcues.HttpSource(base, budget, _SESSION, headers=headers, verify=False)
        else:
            src = dexcues.VfsSource(base, budget, _vfs_open)
        r = dexcues.load(src)
    except dexcues.CuesError as e:
        r = {'ok': False, 'reason': e.reason, 'detail': e.detail, 'stats': budget.stats()}
    out = {'ok': False, 'reason': r.get('reason', ''), 'detail': r.get('detail', ''), 'stats': r.get('stats')}
    if r.get('ok'):
        t = dexcues.pick_reference(r['tracks'])
        if t:
            out = {'ok': True, 'starts': [c[0] for c in t['cues']], 'stats': r['stats'], 'via': r.get('via'),
                   'track': {'number': t['number'], 'lang': t['lang'] or '?', 'codec': t['codec'], 'cues': len(t['cues']),
                             'estimated': t['estimated'], 'name': t['name'], 'kind': t.get('kind', 'text')}}
        else:
            out['reason'] = 'no_usable_text_track'
            out['detail'] = ' '.join('%s:%d' % (x['lang'] or '?', len(x['cues'])) for x in r['tracks'])[:120]
    ttl = _REF_OK_TTL if out['ok'] else (60 if out['reason'] in _TRANSIENT else _REF_FAIL_TTL)
    with _REF_LOCK:
        _REF_CACHE[media_url] = (time.time() + ttl, out)
        while len(_REF_CACHE) > 6:
            _REF_CACHE.pop(min(_REF_CACHE, key=lambda k: _REF_CACHE[k][0]))
    return out, False


_FPS_NAMES = ((25.0 / 23.976, '23.976 و25'), (25.0 / 24.0, '24 و25'), (24.0 / 23.976, '23.976 و24'))


def _describe(res, starts):
    sc = res['scale']
    if abs(sc - 1.0) > 0.0005:
        # الإزاحة تتغير على طول الفيلم، فنذكر فرق السرعة بدل رقم ثواني واحد
        for ratio, label in _FPS_NAMES:
            if abs(sc - ratio) < 0.002 or abs(sc - 1.0 / ratio) < 0.002:
                return _t('صحّحت فرق السرعة بين %s فريم') % _t(label)
        return _t('صحّحت فرق السرعة (%.2f%%)') % ((sc - 1.0) * 100)
    word = _t('قدّمت') if res['offset_ms'] < 0 else _t('أخّرت')
    return _t('%s الترجمة %.1f ث') % (word, abs(res['offset_ms']) / 1000.0)


def _apply(player, path):
    player.setSubtitles(path)
    player.showSubtitles(True)


def _current_raw():
    """AUTOSYNC_V573: الترجمة الشغالة من كودي (JSON-RPC): (index, name, enabled) أو None."""
    try:
        players = json.loads(xbmc.executeJSONRPC(json.dumps(
            {'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetActivePlayers'}))).get('result') or []
        pid = next((p.get('playerid') for p in players if (p or {}).get('type') == 'video'), None)
        if pid is None:
            return None
        r = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'Player.GetProperties',
                                                       'params': {'playerid': pid, 'properties': ['currentsubtitle', 'subtitleenabled']}}))).get('result') or {}
        cur = r.get('currentsubtitle') or {}
        return cur.get('index'), str(cur.get('name') or ''), bool(r.get('subtitleenabled'))
    except Exception:
        return None


def _file_stat(path):
    try:
        if path.startswith('/') or (len(path) > 2 and path[1] == ':'):
            st = os.stat(path)
            return [st.st_mtime, st.st_size]
        st = xbmcvfs.Stat(path)
        return [float(st.st_mtime()), int(st.st_size())]
    except Exception:
        return None


def _viewer_moved(job):
    """المشاهد اختار ترجمة ثانية أو طفاها بعد ما انطلبت المزامنة؟
    source: كودي يكتب الاختيار الجاي فوق نفس الملف (TempSubtitle) وبنفس رقم الترجمة، فنشيك على الملف.
    expect: الترجمة الشغالة وقت الطلب (مهام مراقب الترجمات)."""
    src = job.get('source') or {}
    if src.get('path') and _file_stat(src['path']) != list(src.get('stat') or []):
        # انكتب من جديد: لو بنفس المحتوى (نفس الاختيار مرة ثانية) ما تغير شي
        try:
            p = src['path']
            if p.startswith('/') or (len(p) > 2 and p[1] == ':'):
                data = _read_bytes(p)
            else:
                f = xbmcvfs.File(p)
                try:
                    data = bytes(f.readBytes(_MAX_SUB_BYTES + 1))
                finally:
                    f.close()
            if not data or hashlib.sha1(data).hexdigest() != job.get('sub_hash'):
                return True
        except Exception:
            return True
    exp = job.get('expect')
    if not exp:
        return False
    cur = _current_raw()
    if cur is None:
        return True
    index, name, enabled = cur
    return (not enabled) or index != exp.get('index') or name != exp.get('name')


def attach_source(sub_hash, media_url, path, stat):
    """مراقب الترجمات لقى نسخة كودي من اختيار DexSubtitles: نربطها بمهمته، فلو انكتب فوقها
    اختيار جديد قبل ما تخلص المزامنة ما تنطبق فوقه."""
    key = _job_key(sub_hash, media_url)
    job_path = os.path.join(QUEUE_DIR, '%s.json' % key)
    with _WORK_LOCK:   # ما نلمس مهمة شغالة، ولا نرجّع ملف مهمة خلصت وانحذفت
        if key in _RUNNING or not os.path.exists(job_path):
            return
        try:
            with io.open(job_path, 'r', encoding='utf-8') as f:
                job = json.load(f)
            if job.get('source'):
                return
            job['source'] = {'path': path, 'stat': list(stat or [])}
            with io.open(job_path + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(job, f, ensure_ascii=False)
            os.replace(job_path + '.tmp', job_path)
        except Exception:
            pass


def _job_notify(job, msg, kind):
    """AUTOSYNC_V573: ترجمة شغّلها كودي لحاله (origin=startup) نبلغ عنها بس لو انضبطت فعلاً."""
    if (job or {}).get('origin') == 'startup' and kind != 'applied':
        return
    _notify(msg, kind != 'skip')


def _analyze(raw, name, media_url, key, ext, use_marker=True):
    """AUTOSYNC_V573: قلب المزامنة، مشترك بين الطابور والزر اليدوي.
    يرجّع dict: status = cached | known | few | noref | in_sync | unsure | synced، و path للنسخة المتزامنة، و msg."""
    cached = os.path.join(CACHE_DIR, key + ext)
    marker = os.path.join(CACHE_DIR, key + '.skip')
    if os.path.exists(cached) and os.path.getsize(cached) > 20:
        _log('%s: cache hit' % key[:8])
        return {'status': 'cached', 'path': cached, 'msg': _t('طبّقت نسخة متزامنة محفوظة لهذي الترجمة')}
    if use_marker and os.path.exists(marker):
        _log('%s: known result, original kept' % key[:8])
        try:
            with io.open(marker, 'r', encoding='utf-8') as f:
                known = f.read().strip()
        except Exception:
            known = ''
        return {'status': 'known', 'path': None, 'msg': (known or _t('ما انضبطت في المرة الماضية، خليت الأصل')) + _t(' (نفس نتيجة قبل)')}

    starts = dexalign.parse_starts(raw, name)
    if len(starts) < 30:
        _log('%s: only %d timed lines, original kept' % (key[:8], len(starts)))
        return {'status': 'few', 'path': None, 'msg': _t('الترجمة سطورها قليلة (%d) وما تكفي للمطابقة') % len(starts)}
    ref, from_mem = _reference(media_url)
    if not ref['ok']:
        _log('%s: no reference (%s %s) %s, original kept' % (key[:8], ref.get('reason'), ref.get('detail') or '',
                                                              '' if from_mem else dexcues.fmt_stats(ref.get('stats'))))
        return {'status': 'noref', 'path': None, 'msg': _reason_text(ref.get('reason')) + _t('، خليت الترجمة كما هي')}
    tr = ref['track']
    t1 = time.time()
    res = dexalign.align(ref['starts'], starts)
    min_match = _setting_int('autosync_min_match', 45, 20, 95) / 100.0
    decision = dexalign.decide(res, min_match=min_match)
    _log('%s: ref #%s %s %s %d %s%s %s | match %d%% rival %d%% offset %dms scale %.6f align %.2fs -> %s' % (
        key[:8], tr['number'], tr['lang'], tr['codec'], tr['cues'],
        'image events' if tr.get('kind') == 'image' else 'cues',
        (', %d estimated' % tr['estimated']) if tr['estimated'] and tr.get('kind') != 'image' else '',
        '(index cached)' if from_mem else dexcues.fmt_stats(ref.get('stats')),
        round(res['matched'] * 100), round(res['rival'] * 100), res['offset_ms'], res['scale'],
        time.time() - t1, decision))
    if decision != 'sync':
        pct = round(res['matched'] * 100)
        if decision == 'in_sync':
            why = _t('الترجمة مضبوطة أصلاً (تطابق %d%%)') % pct
        elif res.get('reason'):
            why = _t('ما لقيت تطابق بين الترجمة والملف، خليت الأصل')
        elif res['matched'] < min_match:
            why = _t('التطابق %d%% أقل من %d%% المطلوبة، غالباً الترجمة لنسخة ثانية، خليت الأصل') % (pct, round(min_match * 100))
        else:
            why = _t('التطابق %d%% بس مو واضح عن البدائل، خليت الأصل') % pct
        try:
            with io.open(marker, 'w', encoding='utf-8') as f:
                f.write(why)
        except Exception:
            pass
        return {'status': 'in_sync' if decision == 'in_sync' else 'unsure', 'path': None, 'msg': why}
    out = dexalign.apply_transform(raw, res['scale'], res['offset_ms'], name)
    _write_bytes(cached, out)
    return {'status': 'synced', 'path': cached,
            'msg': _t('%s (تطابق %d%%)') % (_describe(res, starts), round(res['matched'] * 100))}


def _process_job(job, player):
    key = job.get('key', '')
    subtitle_path = job.get('subtitle_path') or ''
    media_url = job.get('media_url') or ''
    if not (subtitle_path and os.path.exists(subtitle_path) and _media_kind(media_url)):
        return False
    if not _still_same_playback(player, job):
        _log('drop stale job %s' % key[:8])
        return False
    if not _is_latest(job):
        _log('drop %s: a newer subtitle was chosen' % key[:8])
        return False
    raw = _read_bytes(subtitle_path)
    if not raw or len(raw) > _MAX_SUB_BYTES or hashlib.sha1(raw).hexdigest() != job.get('sub_hash'):
        _log('drop %s: subtitle file changed or unreadable' % key[:8])
        return False
    t0 = time.time()
    a = _analyze(raw, os.path.basename(subtitle_path), media_url, key, _sub_ext(subtitle_path))
    if a['status'] in ('cached', 'synced'):
        if not _still_same_playback(player, job):
            return False
        if not _is_latest(job):
            _log('%s: synced, but a newer subtitle was chosen; not applied' % key[:8])
            return False
        try:
            with io.open(os.path.join(QUEUE_DIR, '%s.json' % key), 'r', encoding='utf-8') as f:
                job['source'] = json.load(f).get('source') or job.get('source')
        except Exception:
            pass
        if _viewer_moved(job):
            _log('%s: synced, but the viewer changed or hid the subtitle; not applied' % key[:8])
            return False
        try:
            _apply(player, a['path'])
        except Exception as e:
            _log('apply failed: %s' % e, xbmc.LOGWARNING)
            return False
        _log('%s: applied in %.1fs (%s)' % (key[:8], time.time() - t0, job.get('origin') or 'dexsubs'))
        _job_notify(job, a['msg'], 'applied')
        return True
    if _is_latest(job):
        _job_notify(job, a['msg'], 'info' if a['status'] == 'in_sync' else 'skip')
    return False


def sync_now(subtitle_path, media_url, name='', progress=None):
    """AUTOSYNC_V573: مزامنة فورية لزر «زامن الترجمة الشغالة».
    ما تلمس المشغل: كودي نفسه يطبّق الملف اللي يرجع من نافذة الترجمات."""
    if not _media_kind(media_url):
        return {'status': 'nomedia', 'path': None, 'msg': _t('المصدر بث مباشر أو ملف ما ينقرى فهرسه (مو MKV)، فما فيه مزامنة')}
    try:
        raw = _read_bytes(subtitle_path)
    except Exception:
        raw = b''
    if not raw or len(raw) > _MAX_SUB_BYTES:
        return {'status': 'bad', 'path': None, 'msg': _t('ما قدرت أقرأ ملف الترجمة')}
    key = _job_key(hashlib.sha1(raw).hexdigest(), media_url)
    _mark_latest(media_url, key)  # أي مهمة أقدم بالطابور ما تغلب هذا الاختيار
    if progress:
        try:
            progress(35, _t('أقرأ توقيت الفيديو وأطابق عليه…'))
        except Exception:
            pass
    a = _analyze(raw, name or os.path.basename(subtitle_path), media_url, key, _sub_ext(subtitle_path), use_marker=False)
    _log('%s: manual sync -> %s' % (key[:8], a['status']))
    return a


# ── AUTOSYNC_V573: الملفات اللي نرجّعها لكودي بنفسنا ما يعيد مراقب الترجمات معالجتها ──
# ── AUTOPICK_V576 (vendored from DexSubtitles 5.7.6 for Dex Hub 5.10.290): scoring without
#    applying, for the player buttons (subsync/osdflow.py) ──
def reference(media_url):
    """مرجع التوقيت من فهرس الفيديو (بذاكرة نفس الفيديو). {'ok': False, 'reason': ...} لو ما فيه."""
    if not _media_kind(media_url):
        return {'ok': False, 'reason': 'nomedia'}
    try:
        return _reference(media_url)[0]
    except Exception as e:
        _log('reference failed: %s' % e, xbmc.LOGWARNING)
        return {'ok': False, 'reason': 'error'}


def min_match():
    return _setting_int('autosync_min_match', 45, 20, 95) / 100.0


def score(raw, name, media_url, ref=None):
    """يقيّم ترجمة على مرجع الفيديو بدون ما يطبق ولا يكتب شي. يرجّع dict:
    status = bad | few | noref | unsure | in_sync | synced، matched و rival (0..1) و scale و offset_ms،
    data = النص المصحح (synced) أو الأصل (in_sync)، reason سبب غياب المرجع، desc وصف التصحيح."""
    out = {'status': 'bad', 'matched': 0.0, 'rival': 0.0, 'hits': 0, 'scale': 1.0, 'offset_ms': 0,
           'lines': 0, 'reason': '', 'data': None, 'desc': ''}
    if not raw or len(raw) > _MAX_SUB_BYTES:
        return out
    try:
        starts = dexalign.parse_starts(raw, name)
    except Exception:
        starts = []
    out['lines'] = len(starts)
    if len(starts) < 30:
        out['status'] = 'few'
        return out
    if ref is None:
        ref = reference(media_url)
    if not ref.get('ok'):
        out['status'] = 'noref'
        out['reason'] = ref.get('reason') or ''
        return out
    t1 = time.time()
    res = dexalign.align(ref['starts'], starts)
    decision = dexalign.decide(res, min_match=min_match())
    out.update({'matched': res['matched'], 'rival': res['rival'], 'hits': res['hits'],
                'scale': res['scale'], 'offset_ms': res['offset_ms']})
    if decision == 'sync':
        out['status'] = 'synced'
        out['data'] = dexalign.apply_transform(raw, res['scale'], res['offset_ms'], name)
        out['desc'] = _describe(res, starts)
    elif decision == 'in_sync':
        out['status'] = 'in_sync'
        out['data'] = raw
    else:
        out['status'] = 'unsure'
    _log('score %s: match %d%% rival %d%% offset %dms scale %.6f align %.2fs -> %s' % (
        (name or '')[:40], round(res['matched'] * 100), round(res['rival'] * 100), res['offset_ms'],
        res['scale'], time.time() - t1, out['status']))
    return out


def store_synced(raw, media_url, ext, data):
    """النسخة المتزامنة في الكاش بنفس مفتاح الطابور، فلو اخترت نفس الترجمة بعدين تنطبق على طول."""
    key = _job_key(hashlib.sha1(raw).hexdigest(), media_url)
    path = os.path.join(CACHE_DIR, key + (ext if ext in _SUB_EXTS else '.srt'))
    _write_bytes(path, data)
    return path


def claim_latest(raw, media_url):
    """الترجمة اللي طبقها زر المشغل هي آخر اختيار: أي مهمة أقدم بالطابور ما تنطبق فوقها."""
    try:
        _mark_latest(media_url, _job_key(hashlib.sha1(raw).hexdigest(), media_url))
    except Exception:
        pass


# ── AUTOSYNC_V573: الملفات اللي نرجّعها لكودي بنفسنا ما يعيد مراقب الترجمات معالجتها ──


def _handled_path(sub_hash):
    return os.path.join(QUEUE_DIR, 'handled_%s' % (sub_hash or '')[:24])


def note_handled(path, is_ai=False, live=False):
    """ملف رجع لكودي من DexSubtitles (اختيار من القائمة أو زر المزامنة). كودي ينسخه باسم ثاني
    (TempSubtitle أو جنب الفيديو)، فنسجل بصمة محتواه ونوعه عشان مراقب الترجمات يعرفه، حتى
    لو رجع كودي حمّل النسخة المحفوظة بعدين (ترجمة AI تبقى مستثناة لو إعدادها مطفي)."""
    now = time.time()
    try:
        raw = _read_bytes(xbmcvfs.translatePath(path or ''))
        if raw:
            _write_bytes(_handled_path(hashlib.sha1(raw).hexdigest()),
                         json.dumps({'t': now, 'ai': bool(is_ai)}).encode('ascii'))
    except Exception:
        pass
    if live:
        try:
            _write_bytes(os.path.join(QUEUE_DIR, 'last_live'), str(now).encode('ascii'))
        except Exception:
            pass


def handled_info(sub_hash):
    """{'t': وقت، 'ai': ترجمة AI؟} لو الملف رجع من DexSubtitles، وإلا None."""
    try:
        with io.open(_handled_path(sub_hash), 'r', encoding='ascii') as f:
            d = json.loads(f.read() or '{}')
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def last_live_pick_at():
    try:
        return os.path.getmtime(os.path.join(QUEUE_DIR, 'last_live'))
    except Exception:
        return 0.0


def notify_always(msg, ok):
    """تنبيه زر المزامنة: يطلع دايماً (المستخدم ضغط الزر بنفسه)، بغض النظر عن إعدادات التنبيه."""
    try:
        xbmcgui.Dialog().notification('Dex Hub AutoSync', msg,
                                      xbmcgui.NOTIFICATION_INFO if ok else xbmcgui.NOTIFICATION_WARNING, 4000)
    except Exception:
        pass


def defer_notify(msg, ok):
    """تنبيه زر المزامنة تعرضه خدمة الإضافة بعد ثانية (بعد تنبيه كودي «Failed to download subtitle»
    لما الزر يطبق الترجمة بنفسه وما يرجع ملف لنافذة الترجمات)."""
    try:
        _write_bytes(os.path.join(QUEUE_DIR, 'notice'),
                     json.dumps({'t': time.time(), 'msg': str(msg), 'ok': bool(ok)}, ensure_ascii=False).encode('utf-8'))
    except Exception:
        notify_always(msg, ok)


def flush_notice():
    p = os.path.join(QUEUE_DIR, 'notice')
    try:
        if not os.path.exists(p) or time.time() - os.path.getmtime(p) < 0.8:
            return
        with io.open(p, 'r', encoding='utf-8') as f:
            d = json.loads(f.read() or '{}')
        os.remove(p)
        if time.time() - float(d.get('t') or 0) < 120:
            notify_always(d.get('msg') or '', bool(d.get('ok')))
    except Exception:
        try:
            os.remove(p)
        except Exception:
            pass


def _run_job_thread(job, player, path, key):
    try:
        _process_job(job, player)
    except Exception as e:
        _log('job %s failed: %s' % (key[:8], e), xbmc.LOGWARNING)
    finally:
        with _WORK_LOCK:
            try:
                os.remove(path)
            except Exception:
                pass
            _RUNNING.discard(key)


def _clean_cache():
    now = time.time()
    if now - _LAST_CLEAN[0] < 3600:
        return
    _LAST_CLEAN[0] = now
    try:
        for n in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, n)
            if now - os.path.getmtime(p) > 7 * 86400:
                os.remove(p)
    except Exception:
        pass
    # AUTOSYNC_V573: علامات الملفات المعالجة ونسخ مراقب الترجمات
    for folder, prefix, age in ((QUEUE_DIR, 'handled_', 7 * 86400), (WATCH_DIR, '', 2 * 86400)):
        try:
            for n in os.listdir(folder):
                p = os.path.join(folder, n)
                if n.startswith(prefix) and now - os.path.getmtime(p) > age:
                    os.remove(p)
        except Exception:
            pass


def process_queue_once(player):
    """يبدأ مهمة وحدة على الأكثر بدون ما يوقف حلقة خدمة Kodi."""
    if not _setting_bool('autosync_enabled', True):
        return False
    _clean_cache()
    with _WORK_LOCK:
        if _RUNNING:
            return False
        try:
            names = [x for x in os.listdir(QUEUE_DIR) if x.endswith('.json')]
        except Exception:
            names = []
        now = time.time()
        jobs = []
        for name in names:
            path = os.path.join(QUEUE_DIR, name)
            try:
                with io.open(path, 'r', encoding='utf-8') as f:
                    jobs.append((name, path, json.load(f)))
            except Exception:
                try:
                    os.remove(path)
                except Exception:
                    pass
        # AUTOSYNC_V572: الأحدث أول، والأقدم ينحذف لحاله لأنه ما عاد آخر اختيار
        jobs.sort(key=lambda x: -float(x[2].get('created_at') or 0))
        for name, path, job in jobs:
            created = float(job.get('created_at') or now)
            if now - created > _JOB_TTL or int(job.get('v') or 1) < 2:
                try:
                    os.remove(path)
                except Exception:
                    pass
                continue
            if now - created < _MIN_JOB_AGE:
                continue
            key = job.get('key') or name
            _RUNNING.add(key)
            threading.Thread(target=_run_job_thread, args=(job, player, path, key), daemon=True).start()
            return True
    return False
