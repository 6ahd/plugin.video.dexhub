# -*- coding: utf-8 -*-
"""Vendored from DexSubtitles 5.7.6 (osd.py, OSD_V576) for Dex Hub 5.10.290: the player's
Sync subtitle / Next subtitle buttons work without DexSubtitles (subsync/buttons.py hands them to
DexSubtitles when it is installed with its osd.py). Candidates come from Dex Hub's subtitle broker.

OSD_V576: أزرار المشغل لـ DexSubtitles، بدون ملفات تنحقن في مجلد الإضافة.

    RunScript(service.subtitles.dexworld,best)   أفضل ترجمة متزامنة (الزر الأساسي)
    RunScript(service.subtitles.dexworld,sync)   زامن الترجمة الشغالة
    RunScript(service.subtitles.dexworld,next)   الترجمة التالية (وآخرها بدون ترجمة)
    RunScript(service.subtitles.dexworld,pick)   اختر ترجمة: ترجمات المشغل ونتائج DexSubtitles
    RunScript(service.subtitles.dexworld)        قائمة بالأوامر

best: لو فيه ترجمة خارجية شغالة نزامنها، ولو انضبطت أو كانت مضبوطة خلاص. لو ما انضبطت (تطابق ضعيف)
أو ما فيه ترجمة: نحمّل بالخلفية لين 6 ترجمات عربية (نفس بحث DexSubtitles للفيديو الشغال، وملفات
جنب الفيديو أو مرفقة بالمصدر) خلال 45 ثانية تقريباً، ونقيس كل وحدة على الترجمة المدمجة بمحرك
AutoSync، ونطبق الأعلى تطابقاً فوق الحد الآمن (autosync_min_match) بعد تصحيح توقيتها.
لو الفيديو ما فيه مرجع توقيت (MP4 أو HLS أو بث): نختار الأقرب لاسم نسخة الفيديو ونقول إن التوقيت ما يتأكد.
ما نطبق تطابق أضعف فوق أقوى، ولو غيّرت الترجمة بنفسك وإحنا نشتغل ما نطبق فوق اختيارك.
"""
import io
import os
import re
import sys
import time
import gzip
import hashlib
import zipfile
import threading
from urllib.parse import parse_qsl, urlsplit, unquote

import xbmc
import xbmcgui

from . import autosync
from . import dexwatch
from . import dexpick

BUDGET_S = 45          # كل شغل «أفضل ترجمة» من الضغطة لين التطبيق تقريباً
MAX_DL = 6             # أقصى عدد ترجمات نحمّلها من DexSubtitles
MAX_LOCAL = 6          # وملفات جنب الفيديو أو مرفقة بالمصدر (ما تكلف تحميل غالباً)
PARALLEL = 3
MAX_BYTES = 8 * 1024 * 1024
_BUSY_PROP = 'dexhub.subsync.busy'
_SUB_EXTS = ('.srt', '.ass', '.ssa', '.vtt')


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] subtitle buttons: %s' % msg, level)


def _dex():
    """Dex Hub has no DexSubtitles service module: the broker searches (_Search)."""
    return None


def _session():
    return autosync._SESSION


# ── تنبيهات بلغة كودي ──
_AR = []


def _is_ar():
    """Dex Hub's language (Settings), as the rest of Dex Hub."""
    if not _AR:
        try:
            from ..settings_cache import cached_addon
            _AR.append((cached_addon().getSetting('ui_language') or 'English').strip().lower().startswith('ar'))
        except Exception:
            _AR.append(False)
    return _AR[0]


def _pref():
    """The first of Dex Hub's subtitle languages (ar by default), as 'ar' and as Kodi's 'ara'."""
    try:
        from ..subtitle_broker import _preferred_languages
        code = (_preferred_languages() or ['ar'])[0]
    except Exception:
        code = 'ar'
    code = str(code or 'ar').lower().split('-')[0]
    return code, (dexwatch.norm_lang(code) or 'ara')


def T(ar, en):
    return ar if _is_ar() else en


def _note(ar, en, ok=True, ms=4500):
    try:
        xbmcgui.Dialog().notification('Dex Hub', T(ar, en),
                                      xbmcgui.NOTIFICATION_INFO if ok else xbmcgui.NOTIFICATION_WARNING, ms)
    except Exception:
        pass


def _short(name, n=38):
    s = re.sub(r'\s+', ' ', str(name or '')).strip()
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def _pct(r):
    return int(round((r or {}).get('matched', 0) * 100))


_WHY = (
    (('nomedia',), 'بث أو رابط ما ينقرى فهرسه', 'stream without a readable index'),
    (('not_mkv',), 'الملف مو MKV', 'not an MKV file'),
    (('no_subtitle_tracks', 'no_usable_subtitles', 'no_usable_text_track'),
     'ما فيه ترجمة مدمجة نقارن عليها', 'no embedded subtitle to compare with'),
    (('no_range',), 'الخادم ما يسمح بالقراءة الجزئية', 'the server has no range support'),
    (('no_cues', 'no_segment', 'unknown_size', 'truncated', 'short_file', 'no_tracks'),
     'فهرس الملف ناقص', 'the file index is incomplete'),
    (('cues_too_large', 'element_too_large'), 'فهرس الملف كبير جداً', 'the file index is too large'),
)


def _why(reason):
    reason = str(reason or '')
    for keys, ar, en in _WHY:
        if reason in keys:
            return T(ar, en)
    if reason in ('timeout', 'http_io', 'io_error', 'budget_requests', 'budget_bytes', 'error') or reason.startswith('http_'):
        return T('ما قدرت أقرأ فهرس الملف الحين', "couldn't read the file index now")
    return T('ما فيه مرجع توقيت', 'no timing reference')


_FPS = ((25.0 / 23.976, '23.976/25'), (25.0 / 24.0, '24/25'), (24.0 / 23.976, '23.976/24'))


def _desc(r):
    sc = r.get('scale') or 1.0
    if abs(sc - 1.0) > 0.0005:
        for ratio, label in _FPS:
            if abs(sc - ratio) < 0.002 or abs(sc - 1.0 / ratio) < 0.002:
                return T('صحّحت فرق السرعة %s' % label, 'fixed the %s fps speed' % label)
        return T('صحّحت فرق السرعة', 'fixed the speed')
    sec = abs(r.get('offset_ms') or 0) / 1000.0
    if (r.get('offset_ms') or 0) < 0:
        return T('قدّمتها %.1f ث' % sec, 'moved %.1fs earlier' % sec)
    return T('أخّرتها %.1f ث' % sec, 'moved %.1fs later' % sec)


# ── شريط تقدم صغير (مو نافذة تحجب) ──
class _Bar(object):
    def __init__(self, msg):
        try:
            self.d = xbmcgui.DialogProgressBG()
            self.d.create('Dex Hub', msg)
        except Exception:
            self.d = None

    def update(self, pct, msg):
        try:
            if self.d:
                self.d.update(max(0, min(100, int(pct))), 'Dex Hub', msg)
        except Exception:
            pass

    def close(self):
        try:
            if self.d:
                self.d.close()
        except Exception:
            pass
        self.d = None


# ── قفل: ضغطتين ورا بعض ما يشغلون شغلين ──
def _acquire():
    w = xbmcgui.Window(10000)
    try:
        t = float(w.getProperty(_BUSY_PROP) or 0)
    except Exception:
        t = 0.0
    if time.time() - t < 120:
        return False
    w.setProperty(_BUSY_PROP, str(time.time()))
    return True


def _release():
    try:
        xbmcgui.Window(10000).clearProperty(_BUSY_PROP)
    except Exception:
        pass


# ── المشغل ──
def _media():
    try:
        p = xbmc.Player()
        return p.getPlayingFile() if p.isPlayingVideo() else ''
    except Exception:
        return ''


def _sig(cur):
    if not cur:
        return None
    return (cur.get('index'), cur.get('raw_name'), cur.get('enabled'))


def _moved(media, sig):
    """الفيديو تغير أو المشاهد غيّر الترجمة وإحنا نشتغل؟"""
    return _media() != media or _sig(dexwatch.current_subtitle()) != sig


def _streams():
    pid = dexwatch.video_player_id()
    if pid is None:
        return None, [], {}, False
    r = dexwatch._jsonrpc('Player.GetProperties', {'playerid': pid,
                                                   'properties': ['subtitles', 'currentsubtitle', 'subtitleenabled']}) or {}
    return pid, list(r.get('subtitles') or []), (r.get('currentsubtitle') or {}), bool(r.get('subtitleenabled'))


_AR_LANG = {'ara': 'العربية', 'eng': 'الإنجليزية', 'fre': 'الفرنسية', 'spa': 'الإسبانية', 'ger': 'الألمانية',
            'ita': 'الإيطالية', 'por': 'البرتغالية', 'pob': 'البرتغالية', 'rus': 'الروسية', 'tur': 'التركية',
            'per': 'الفارسية', 'urd': 'الأردية', 'hin': 'الهندية', 'jpn': 'اليابانية', 'kor': 'الكورية',
            'chi': 'الصينية', 'ind': 'الإندونيسية', 'may': 'الملايوية', 'dut': 'الهولندية', 'heb': 'العبرية',
            'gre': 'اليونانية', 'pol': 'البولندية', 'swe': 'السويدية', 'tha': 'التايلندية', 'vie': 'الفيتنامية'}


def _lang_label(code):
    c = dexwatch.norm_lang(code)
    if not c or c in ('und', 'unk', 'zxx', 'mis'):
        return T('بدون لغة', 'Unknown')
    if _is_ar() and c in _AR_LANG:
        return _AR_LANG[c]
    n = dexwatch._C2NAME.get(c)
    return n.title() if n else c


def _stream_label(s):
    name = str(s.get('name') or '').strip()
    ext = False
    for lab in dexwatch._external_labels():
        if lab and name.lower().endswith(lab.lower()):
            name = name[:-len(lab)].strip()
            ext = True
            break
    lang = _lang_label(s.get('language'))
    bits = [lang]
    if name and name.lower() != lang.lower():
        bits.append(_short(name, 50))
    if s.get('isforced'):
        bits.append(T('إجبارية', 'forced'))
    if ext:
        bits.append(T('خارجية', 'external'))
    return ' · '.join(bits)


def _set_stream(pid, index, pos):
    r = dexwatch._jsonrpc('Player.SetSubtitle', {'playerid': pid, 'subtitle': index, 'enable': True})
    if r != 'OK':
        p = xbmc.Player()
        p.setSubtitleStream(pos)
        p.showSubtitles(True)


def _set_off(pid):
    r = dexwatch._jsonrpc('Player.SetSubtitle', {'playerid': pid, 'subtitle': 'off'})
    if r != 'OK':
        xbmc.Player().showSubtitles(False)


# ── الملفات ──
def _read(path):
    try:
        with io.open(path, 'rb') as f:
            return f.read(MAX_BYTES + 1)
    except Exception:
        return b''


def _is_html(data):
    head = data[:512].lstrip().lower()
    return head.startswith(b'<!doctype') or head.startswith(b'<html')


def _ext_for(data, hint=''):
    head = data[:6000]
    if b'[Events]' in head or b'[Script Info]' in head:
        return '.ass'
    if head.lstrip(b'\xef\xbb\xbf \r\n\t').startswith(b'WEBVTT'):
        return '.vtt'
    e = os.path.splitext(str(hint or '').split('?', 1)[0].split('|', 1)[0])[1].lower()
    return e if e in _SUB_EXTS else '.srt'


def _unpack(data, want=None):
    if data[:2] == b'PK':
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if os.path.splitext(n)[1].lower() in _SUB_EXTS]
        if not names:
            return b''
        pick = names[0]
        if want and len(names) > 1:
            rx = re.compile(r'(?<![a-z0-9])s0*%d[ ._-]?e0*%d(?!\d)' % want, re.I)
            pick = next((n for n in names if rx.search(n)), pick)
        return z.read(pick)[:MAX_BYTES + 1]
    if data[:2] == b'\x1f\x8b':
        return gzip.decompress(data)[:MAX_BYTES + 1]
    return data


def _want_episode(req):
    m = re.search(r'/series/[^/?]*?:(\d+):(\d+)\.json', str(req or ''))
    return (int(m.group(1)), int(m.group(2))) if m else None


def _fetch(url, timeout=20, want=None):
    from urllib.request import Request, urlopen
    raw_url, headers = str(url or ''), {'User-Agent': 'DexHub-Subtitles/1 (Kodi)'}
    if '|' in raw_url:
        raw_url, extra = raw_url.split('|', 1)
        headers.update(dict(parse_qsl(extra)))
    if not raw_url.lower().startswith(('http://', 'https://')):
        return dexwatch._read_any(raw_url, _session()) or b''
    with urlopen(Request(raw_url, headers=headers), timeout=max(4, timeout)) as r:
        if getattr(r, 'status', 200) != 200:
            return b''
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return b''
    return _unpack(data, want)


def _named_copy(data, name, ext):
    """النسخة اللي نطبقها: اسم واضح ينتهي بـ .ar عشان كودي يعرف لغتها، في مجلد AutoSync عندنا
    (مراقب الترجمات يعرف إنها لنا وما يعيد معالجتها)."""
    base = re.sub(r'[^\w\-. ]+', ' ', str(name or '')).strip(' .-_')[:60] or 'DexHub'
    base = re.sub(r'(?i)([ ._-](ar|ara|arabic))+$', '', base).strip(' .-_') or 'DexHub'
    lang = _pref()[0]
    path = os.path.join(autosync.WATCH_DIR, '%s.%s%s' % (base, lang, ext))
    if os.path.exists(path) and _read(path) != data:
        path = os.path.join(autosync.WATCH_DIR, '%s.%s.%s%s' % (base, hashlib.sha1(data).hexdigest()[:6], lang, ext))
    if not os.path.isdir(autosync.WATCH_DIR):
        os.makedirs(autosync.WATCH_DIR)
    if not os.path.exists(path):
        tmp = path + '.tmp'
        with io.open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def _apply(data, name, ext, media, ai=False):
    path = _named_copy(data, name, ext)
    p = xbmc.Player()
    p.setSubtitles(path)
    p.showSubtitles(True)
    autosync.claim_latest(data, media)          # مهمة أقدم بالطابور ما تنطبق فوقها
    autosync.note_handled(path, is_ai=ai)
    _log('applied %s' % os.path.basename(path))
    return path


def _current_file(media, cur):
    since = time.time() - 6 * 3600
    cands = dexwatch.collect(media, since=since, own_dir=autosync.TEMP_DIR, deep_since=since)
    pick = dexwatch.identify(cur, cands, media)[0] if (cur and cur.get('external')) else None
    return pick, cands


def _video_texts(media):
    base = str(media or '').split('|', 1)[0]
    texts = [xbmc.getInfoLabel('Player.Filename'), base]
    try:
        u = urlsplit(base)
        texts.append(unquote(u.path))
        texts += [v for k, v in parse_qsl(u.query) if k.lower() in ('filename', 'name', 'file', 'title')]
    except Exception:
        pass
    try:
        texts.append(xbmc.Player().getPlayingItem().getLabel())
    except Exception:
        pass
    return texts


# ── Dex Hub: the subtitle broker searches every subtitle add-on for the playing video ──
class _Search(threading.Thread):
    def __init__(self, dex, deadline):
        threading.Thread.__init__(self)
        self.daemon = True
        self.deadline = deadline
        self.subs = None
        self.req = ''

    def run(self):
        try:
            from ..subtitle_service import _playing_ctx
            from .. import subtitle_broker
            ctx = _playing_ctx()
            if ctx.get('media_type') == 'series' and ctx.get('season') and ctx.get('episode'):
                # _want_episode reads the episode numbers from this (a zip of a season)
                self.req = '/series/x:%s:%s.json' % (ctx['season'], ctx['episode'])
            self.subs = subtitle_broker.search_subtitles(ctx, max_results=60) or []
        except Exception as e:
            _log('search failed: %s' % e, xbmc.LOGWARNING)
            self.subs = []


def _row_ai(row):
    try:
        from ..subtitle_service import _is_ai
        return bool(_is_ai(row))
    except Exception:
        return False


def _provider_cands(dex, subs, req, include_ai, video, show_ai=False):
    """The broker's rows in Dex Hub's first subtitle language, as DexSubtitles' candidates."""
    try:
        from ..subtitle_service import _row_name, _sort_key
    except Exception:
        _row_name, _sort_key = (lambda r: str(r.get('displayName') or 'Subtitle')), (lambda r: 0)
    want = _pref()[0]
    rows = []
    for s in subs or []:
        try:
            lang = str(s.get('lang') or s.get('language') or '').lower().replace('_', '-').split('-')[0]
            if dexwatch.norm_lang(lang) != dexwatch.norm_lang(want):
                continue
            url = str(s.get('url') or '')
            if not url or '/ai_request/' in url.lower():
                continue
            ai = _row_ai(s)
            if ai and not (include_ai or show_ai):
                continue
            rows.append(s)
        except Exception:
            continue
    rows.sort(key=_sort_key)
    out = []
    for i, s in enumerate(rows):
        name = _row_name(s)
        rel = ' '.join(str(s.get(k) or '') for k in ('release', 'filename', 'title') if s.get(k)) or name
        url = str(s.get('url') or '')
        ext = os.path.splitext(url.split('?', 1)[0])[1].lower()
        out.append({'kind': 'provider', 'url': url, 'name': name,
                    'fname': name + (ext if ext in _SUB_EXTS else '.srt'),
                    'provider': str(s.get('sourceName') or 'Dex Hub'),
                    'is_ai': _row_ai(s), 'order': i, 'nscore': dexpick.name_score(video, rel)})
    return out


def _local_cands(media, video, skip_paths):
    """ملفات جنب الفيديو ومجلدات Subs والمجلد المخصص والمرفقة بالمصدر (اللغة من اسمها بقاعدة كودي)."""
    later = time.time() + 60   # ما نبي نسخ special://temp ولا مجلدات الإضافات هنا
    try:
        cands = dexwatch.collect(media, since=later, own_dir='', deep_since=later)
    except Exception:
        return []
    out = []
    for i, c in enumerate(cands):
        if c['where'] not in ('video', 'custom', 'item', 'item-url') or c['path'] in skip_paths:
            continue
        try:
            _t, lang, forced, _n, _p = dexwatch.kodi_parts(media, c['name'] if c['where'] == 'item-url' else c['path'])
        except Exception:
            continue
        if forced or (lang and lang != _pref()[1]):
            continue
        base = os.path.splitext(c['name'])[0]
        out.append({'kind': 'local', 'path': c['path'], 'name': base, 'fname': c['name'], 'provider': c['where'],
                    'is_ai': False, 'order': -100 + i, 'nscore': dexpick.name_score(video, base)})
    out.sort(key=lambda c: (-c['nscore'], c['order']))
    return out


def _download(todo, deadline, bar, want=None):
    sem = threading.Semaphore(PARALLEL)

    def work(c):
        with sem:
            try:
                left = deadline - time.time()
                if left <= 1:
                    return
                if c['kind'] == 'provider':
                    data = _fetch(c['url'], timeout=min(20, left), want=want)
                else:
                    data = dexwatch._read_any(c['path'], _session())
                c['raw'] = data if (data and len(data) <= MAX_BYTES) else b''
            except Exception as e:
                _log('download failed (%s): %s' % (_short(c['name'], 30), e), xbmc.LOGWARNING)
            finally:
                c['done'] = True

    for c in todo:
        threading.Thread(target=work, args=(c,), daemon=True).start()
    mon = xbmc.Monitor()
    n = len(todo)
    while time.time() < deadline:
        done = sum(1 for c in todo if c.get('done'))
        bar.update(30 + 30.0 * done / n, T('أحمّل الترجمات %d من %d…', 'Downloading %d of %d…') % (done, n))
        if done >= n or mon.waitForAbort(0.25):
            break
    return [c for c in todo if c.get('done') and c.get('raw')]


def _usable(cands, skip_hash, include_ai):
    """نشيل المكرر والفاضي وصفحات HTML، والملفات المحلية اللي مو عربية، وAI لو إعدادها مطفي."""
    out, seen = [], set(skip_hash)
    for c in cands:
        raw = c.get('raw') or b''
        if not raw or _is_html(raw):
            continue
        h = hashlib.sha1(raw).hexdigest()
        if h in seen:
            continue
        if c['kind'] == 'local':
            if _pref()[1] == 'ara' and not dexpick.looks_arabic(raw):
                continue
            info = autosync.handled_info(h) or {}
            if info.get('ai'):
                c['is_ai'] = True
        if c['is_ai'] and not include_ai:
            continue
        seen.add(h)
        c['sha'] = h
        c['ext'] = _ext_for(raw, c.get('fname') or '')
        out.append(c)
    return out


# ── best ──
def cmd_best(search=None):
    media = _media()
    if not media:
        _note('شغّل الفيديو أول', 'Play a video first', False)
        return
    dex = _dex()
    deadline = time.time() + BUDGET_S
    if search is None:
        search = _Search(dex, deadline)
        search.start()
    bar = _Bar(T('أدور على أفضل ترجمة…', 'Finding the best subtitle…'))
    try:
        _best(dex, media, search, bar, deadline)
    finally:
        bar.close()


def _best(dex, media, search, bar, deadline):
    include_ai = autosync._setting_bool('autosync_include_ai', False)
    cur = dexwatch.current_subtitle()
    sig = _sig(cur)
    bar.update(5, T('أقرأ توقيت الفيديو…', 'Reading the video timing…'))
    ref = autosync.reference(media)
    has_ref = bool(ref.get('ok'))
    video = dexpick.best_video_info(_video_texts(media))

    cur_on = bool(cur and cur.get('enabled') and cur.get('index') is not None)
    cur_lang = (cur or {}).get('language') or ''
    pref3 = _pref()[1]
    cur_arabic_ext = cur_on and cur.get('external') and cur_lang in ('', pref3, 'und')
    if cur_on and not cur.get('external') and cur_lang == pref3:
        _note('الترجمة العربية المدمجة شغالة، وتوقيتها من الملف نفسه',
              'The embedded Arabic subtitle is playing; it is timed with the file', True)
        return

    # (أ) الترجمة الخارجية الشغالة: نزامنها أول
    baseline = None      # تطابق الشغالة لو انقاست وما انضبطت: البديل لازم يتعداه
    cur_nscore = None    # قرب اسمها من نسخة الفيديو (لما ما فيه مرجع توقيت)
    skip_hash, skip_paths = set(), set()
    if cur_arabic_ext:
        pick, _c = _current_file(media, cur)
        if pick:
            skip_paths.add(pick['path'])
            snap, h = dexwatch.snapshot(pick, autosync.WATCH_DIR, _session())
            raw = _read(snap) if snap else b''
            if raw:
                skip_hash.add(h)
                cur_ai = bool((autosync.handled_info(h) or {}).get('ai'))
                name = os.path.splitext(pick['name'])[0]
                cur_nscore = dexpick.name_score(video, name)
                if has_ref and (include_ai or not cur_ai):
                    bar.update(15, T('أطابق الترجمة الشغالة…', 'Checking the playing subtitle…'))
                    r = autosync.score(raw, pick['name'], media, ref)
                    if r['status'] == 'synced':
                        if _moved(media, sig):
                            _note('غيّرت الترجمة وإحنا نشتغل، خليتها', 'The subtitle changed meanwhile; left it', False)
                            return
                        ext = _ext_for(raw, pick['name'])
                        autosync.store_synced(raw, media, ext, r['data'])
                        _apply(r['data'], name, ext, media, ai=cur_ai)
                        _note('ضبطت الترجمة الشغالة: %s (تطابق %d%%)' % (_desc(r), _pct(r)),
                              'Synced the playing subtitle: %s (%d%% match)' % (_desc(r), _pct(r)))
                        return
                    if r['status'] == 'in_sync':
                        _note('الترجمة الشغالة مضبوطة أصلاً (تطابق %d%%)' % _pct(r),
                              'The playing subtitle is already in sync (%d%% match)' % _pct(r))
                        return
                    if r['status'] == 'unsure':
                        baseline = r['matched']
        if cur_nscore is None and cur.get('name'):
            cur_nscore = dexpick.name_score(video, cur['name'])

    # (ب) مرشحين: بحث DexSubtitles وملفات جنب الفيديو والمرفقة بالمصدر
    bar.update(25, T('أدور على ترجمات عربية…', 'Searching Arabic subtitles…'))
    search.join(max(1.0, deadline - 15 - time.time()))
    provs = _provider_cands(dex, search.subs or [], search.req, include_ai, video)
    provs.sort(key=lambda c: (-c['nscore'], c['order']))
    todo = _local_cands(media, video, skip_paths)[:MAX_LOCAL] + provs[:MAX_DL]
    if not todo:
        if search.is_alive():
            _note('البحث عن الترجمات طوّل، جرّب بعد شوي', 'The subtitle search took too long; try again shortly', False)
        else:
            _note('ما لقيت ترجمات عربية لهذا الفيديو', 'No Arabic subtitles found for this video', False)
        return
    got = _usable(_download(todo, deadline, bar, want=_want_episode(search.req)), skip_hash, include_ai)
    if not got:
        _note('ما قدرت أحمّل ترجمة تصلح', "Couldn't download a usable subtitle", False)
        return

    if has_ref:
        n = len(got)
        for i, c in enumerate(got):
            if time.time() > deadline + 20:
                break
            bar.update(60 + 38.0 * i / n, T('أطابق %d من %d…', 'Checking %d of %d…') % (i + 1, n))
            c['r'] = autosync.score(c['raw'], c['fname'] if c['fname'].lower().endswith(_SUB_EXTS) else 'x' + c['ext'],
                                    media, ref)
        scored = [c for c in got if c.get('r')]
        good = [c for c in scored if c['r']['status'] in ('synced', 'in_sync')]
        if baseline is not None:
            good = [c for c in good if c['r']['matched'] > baseline]
        if not good:
            top = max([_pct(c['r']) for c in scored] + [int(round((baseline or 0) * 100))])
            if cur_arabic_ext:
                _note('ما لقيت ترجمة أضبط من الحالية (أعلى تطابق %d%%)، خليتها' % top,
                      'No subtitle matched better than the current one (best %d%%); kept it' % top, False)
            else:
                _note('ما لقيت ترجمة تطابق توقيت الفيديو (أعلى تطابق %d%%)' % top,
                      "No subtitle matched this video's timing (best %d%%)" % top, False)
            return
        best = max(good, key=lambda c: (c['r']['matched'], c['nscore'], -c['order']))
        r = best['r']
        if _moved(media, sig):
            _note('غيّرت الترجمة وإحنا نشتغل، خليتها', 'The subtitle changed meanwhile; left it', False)
            return
        if r['status'] == 'synced':
            autosync.store_synced(best['raw'], media, best['ext'], r['data'])
        _apply(r['data'], best['name'], best['ext'], media, ai=best['is_ai'])
        if r['status'] == 'synced':
            _note('اخترت «%s» وضبطت توقيتها (تطابق %d%%)' % (_short(best['name']), _pct(r)),
                  'Picked “%s” and fixed its timing (%d%% match)' % (_short(best['name']), _pct(r)))
        else:
            _note('اخترت «%s»، توقيتها مضبوط (تطابق %d%%)' % (_short(best['name']), _pct(r)),
                  'Picked “%s”, in sync (%d%% match)' % (_short(best['name']), _pct(r)))
        return

    # (ج) ما فيه مرجع توقيت: الأقرب لاسم نسخة الفيديو
    why = _why(ref.get('reason'))
    best = max(got, key=lambda c: (c['nscore'], -c['order']))
    if cur_arabic_ext and (best['nscore'] <= (cur_nscore or 0) or best['nscore'] < 25):
        _note('خليت الترجمة الحالية، والتوقيت ما يتأكد (%s)' % why,
              "Kept the current subtitle; timing can't be verified (%s)" % why, False)
        return
    if _moved(media, sig):
        _note('غيّرت الترجمة وإحنا نشتغل، خليتها', 'The subtitle changed meanwhile; left it', False)
        return
    _apply(best['raw'], best['name'], best['ext'], media, ai=best['is_ai'])
    _note('طبّقت «%s» حسب اسم النسخة، والتوقيت ما يتأكد (%s)' % (_short(best['name']), why),
          "Applied “%s” by release name; timing can't be verified (%s)" % (_short(best['name']), why), False)


# ── sync ──
def cmd_sync():
    media = _media()
    if not media:
        _note('شغّل الفيديو أول', 'Play a video first', False)
        return
    dex = _dex()
    cur = dexwatch.current_subtitle()
    pick, cands = _current_file(media, cur)
    chosen = False
    if not pick:
        others = [c for c in cands if c['where'] != 'self']
        others.sort(key=lambda c: (dexwatch._WHERE_RANK.get(c['where'], 0), c['mtime']), reverse=True)
        if not others:
            if cur and cur.get('enabled') and cur.get('index') is not None and not cur.get('external'):
                _note('الشغالة مدمجة وتوقيتها من الملف، وما فيه ترجمة خارجية أزامنها',
                      'The playing subtitle is embedded (timed with the file); no external subtitle to sync', False)
            else:
                _note('ما لقيت ترجمة خارجية أزامنها', 'No external subtitle to sync', False)
            return
        head = T('أي ترجمة تبي أزامنها؟', 'Which subtitle should I sync?')
        idx = xbmcgui.Dialog().select(head, [_short(os.path.splitext(c['name'])[0], 70) for c in others[:25]])
        if idx < 0:
            return
        pick, chosen = others[idx], True
    snap, h = dexwatch.snapshot(pick, autosync.WATCH_DIR, _session())
    raw = _read(snap) if snap else b''
    if not raw:
        _note('ما قدرت أقرأ ملف الترجمة', "Couldn't read the subtitle file", False)
        return
    is_ai = bool((autosync.handled_info(h) or {}).get('ai'))
    if is_ai and not autosync._setting_bool('autosync_include_ai', False):
        _note('ترجمة AI: المزامنة مطفية لها من الإعدادات', 'AI subtitle: sync is off in the settings', False)
        return
    sig = _sig(dexwatch.current_subtitle())
    bar = _Bar(T('أزامن الترجمة…', 'Syncing the subtitle…'))
    try:
        bar.update(30, T('أقرأ توقيت الفيديو وأطابق…', 'Reading the video timing…'))
        r = autosync.score(raw, pick['name'], media)
    finally:
        bar.close()
    name = os.path.splitext(pick['name'])[0]
    ext = _ext_for(raw, pick['name'])
    st = r['status']
    if st in ('synced', 'in_sync') and _moved(media, sig):
        _note('غيّرت الترجمة وإحنا نشتغل، خليتها', 'The subtitle changed meanwhile; left it', False)
        return
    if st == 'synced':
        autosync.store_synced(raw, media, ext, r['data'])
        _apply(r['data'], name, ext, media, ai=is_ai)
        _note('ضبطت التوقيت: %s (تطابق %d%%)' % (_desc(r), _pct(r)), 'Synced: %s (%d%% match)' % (_desc(r), _pct(r)))
    elif st == 'in_sync':
        if chosen:
            _apply(raw, name, ext, media, ai=is_ai)
        _note('الترجمة مضبوطة أصلاً (تطابق %d%%)' % _pct(r), 'Already in sync (%d%% match)' % _pct(r))
    elif st == 'unsure':
        need = int(round(autosync.min_match() * 100))
        if _pct(r) < need:
            _note('ما انضبطت: التطابق %d%% أقل من %d%%، جرّب «أفضل ترجمة»' % (_pct(r), need),
                  "Couldn't sync: %d%% match is under %d%%. Try Best subtitle" % (_pct(r), need), False)
        else:
            _note('ما انضبطت: التطابق %d%% مو واضح، جرّب «أفضل ترجمة»' % _pct(r),
                  "Couldn't sync: %d%% match isn't clear enough. Try Best subtitle" % _pct(r), False)
    elif st == 'noref':
        _note('ما تتزامن: %s' % _why(r['reason']), "Can't sync: %s" % _why(r['reason']), False)
    elif st == 'few':
        _note('الترجمة سطورها قليلة وما تكفي للمطابقة', 'Too few subtitle lines to match', False)
    else:
        _note('ما قدرت أقرأ ملف الترجمة', "Couldn't read the subtitle file", False)


# ── next ──
def cmd_next():
    if not _media():
        _note('شغّل الفيديو أول', 'Play a video first', False, 2000)
        return
    pid, subs, cur, enabled = _streams()
    if pid is None or not subs:
        _note('ما فيه ترجمات في هذا الفيديو', 'No subtitles in this video', False, 2000)
        return
    order = [s.get('index') for s in subs]
    pos = 0
    if enabled and cur.get('index') in order:
        pos = order.index(cur.get('index')) + 1
    if pos >= len(order):
        _set_off(pid)
        _note('بدون ترجمة', 'Subtitles off', True, 1800)
        return
    _set_stream(pid, order[pos], pos)
    _note('%d/%d · %s' % (pos + 1, len(order), _stream_label(subs[pos])),
          '%d/%d · %s' % (pos + 1, len(order), _stream_label(subs[pos])), True, 2200)


# ── pick ──
def cmd_pick():
    media = _media()
    if not media:
        _note('شغّل الفيديو أول', 'Play a video first', False)
        return
    dex = _dex()
    include_ai = autosync._setting_bool('autosync_include_ai', False)
    search = _Search(dex, time.time() + BUDGET_S)
    search.start()
    bar = _Bar(T('أدور على الترجمات…', 'Searching subtitles…'))
    try:
        until = time.time() + 20
        mon = xbmc.Monitor()
        while search.is_alive() and time.time() < until:
            bar.update(100.0 * (20 - (until - time.time())) / 20, T('أدور على الترجمات…', 'Searching subtitles…'))
            if mon.waitForAbort(0.3):
                return
    finally:
        bar.close()
    if _media() != media:
        return
    pid, subs, cur, enabled = _streams()
    video = dexpick.best_video_info(_video_texts(media))
    provs = _provider_cands(dex, search.subs or [], search.req, include_ai, video, show_ai=True)
    provs.sort(key=lambda c: (-c['nscore'], c['order']))

    rows, labels, pre = [], [], 0
    rows.append(('best', None))
    labels.append('★ ' + T('تلقائي: أفضل ترجمة متزامنة', 'Auto: best synced subtitle'))
    for pos, s in enumerate(subs):
        on = enabled and s.get('index') == cur.get('index')
        if on:
            pre = len(rows)
        rows.append(('stream', (s.get('index'), pos)))
        labels.append(('● ' if on else '') + _stream_label(s))
    if subs:
        if not enabled:
            pre = len(rows)
        rows.append(('off', None))
        labels.append(T('بدون ترجمة', 'Subtitles off'))
    for c in provs[:40]:
        rows.append(('prov', c))
        labels.append('%s  (%s)%s' % (_short(c['name'], 70), c['provider'], ' [AI]' if c['is_ai'] else ''))
    if not provs:
        if search.subs is None:
            labels.append(T('البحث عن الترجمات طوّل، النتائج ما وصلت', 'The subtitle search is taking too long'))
        else:
            labels.append(T('ما لقيت ترجمات من إضافات الترجمة', 'No results from the subtitle add-ons'))
        rows.append(('none', None))
    idx = xbmcgui.Dialog().select(T('اختر الترجمة', 'Choose a subtitle'), labels, preselect=pre)
    if idx < 0 or _media() != media:
        return
    kind, val = rows[idx]
    if kind == 'best':
        cmd_best(search=search)
    elif kind == 'stream':
        _set_stream(pid, val[0], val[1])
    elif kind == 'off':
        _set_off(pid)
    elif kind == 'prov':
        _apply_chosen(val, media, include_ai, _want_episode(search.req))


def _apply_chosen(c, media, include_ai, want):
    bar = _Bar(T('أحمّل الترجمة…', 'Downloading the subtitle…'))
    r = None
    try:
        try:
            raw = _fetch(c['url'], timeout=30, want=want)
        except Exception as e:
            _log('download failed: %s' % e, xbmc.LOGWARNING)
            raw = b''
        if not raw or _is_html(raw):
            _note('ما قدرت أحمّل الترجمة', "Couldn't download the subtitle", False)
            return
        ext = _ext_for(raw, c['fname'])
        if include_ai or not c['is_ai']:
            bar.update(50, T('أطابق التوقيت…', 'Matching the timing…'))
            r = autosync.score(raw, 'x' + ext, media)
    finally:
        bar.close()
    if _media() != media:
        return
    nm = _short(c['name'])
    if r and r['status'] == 'synced':
        autosync.store_synced(raw, media, ext, r['data'])
        _apply(r['data'], c['name'], ext, media, ai=c['is_ai'])
        _note('طبّقت «%s» وضبطت توقيتها (تطابق %d%%)' % (nm, _pct(r)),
              'Applied “%s” and fixed its timing (%d%% match)' % (nm, _pct(r)))
        return
    _apply(raw, c['name'], ext, media, ai=c['is_ai'])
    st = (r or {}).get('status')
    if st == 'in_sync':
        _note('طبّقت «%s»، توقيتها مضبوط (تطابق %d%%)' % (nm, _pct(r)), 'Applied “%s”, in sync (%d%% match)' % (nm, _pct(r)))
    elif st == 'unsure':
        _note('طبّقت «%s» بدون تعديل (التطابق %d%% ما يكفي)' % (nm, _pct(r)),
              'Applied “%s” unchanged (%d%% match is too low to adjust)' % (nm, _pct(r)), False)
    elif st == 'noref':
        _note('طبّقت «%s»، والتوقيت ما يتأكد (%s)' % (nm, _why(r['reason'])),
              "Applied “%s”; timing can't be verified (%s)" % (nm, _why(r['reason'])), False)
    elif c['is_ai'] and not include_ai:
        _note('طبّقت «%s» (ترجمة AI بدون مزامنة)' % nm, 'Applied “%s” (AI subtitle, not synced)' % nm)
    else:
        _note('طبّقت «%s»' % nm, 'Applied “%s”' % nm)


# ── بدون أمر: قائمة ──
def _menu():
    opts = [('best', T('أفضل ترجمة متزامنة', 'Best synced subtitle')),
            ('sync', T('زامن الترجمة الشغالة', 'Sync the playing subtitle')),
            ('pick', T('اختر ترجمة', 'Choose a subtitle')),
            ('next', T('الترجمة التالية', 'Next subtitle'))]
    idx = xbmcgui.Dialog().select('Dex Hub', [o[1] for o in opts])
    return opts[idx][0] if idx >= 0 else ''


_COMMANDS = {'best': cmd_best, 'auto': cmd_best, 'sync': cmd_sync, 'next': cmd_next, 'cycle': cmd_next,
             'pick': cmd_pick, 'choose': cmd_pick, 'change': cmd_pick}


def run(argv):
    cmd = str(argv[0] if argv else '').strip().lower()
    for pre in ('action=', 'mode=', 'cmd='):
        if cmd.startswith(pre):
            cmd = cmd[len(pre):]
    if cmd in ('', 'menu'):
        if not _media():
            _note('شغّل الفيديو أول', 'Play a video first', False)
            return
        cmd = _menu()
        if not cmd:
            return
    fn = _COMMANDS.get(cmd)
    if not fn:
        _log('unknown command: %s' % cmd, xbmc.LOGWARNING)
        return
    locked = cmd not in ('next', 'cycle')
    if locked and not _acquire():
        _note('شغّال على طلب قبله، لحظة', 'Still working on the previous request', False, 2500)
        return
    try:
        fn()
    except Exception as e:
        import traceback
        _log('%s failed: %s\n%s' % (cmd, e, traceback.format_exc()), xbmc.LOGWARNING)
        _note('صار خطأ، التفاصيل في سجل كودي', 'Something went wrong; see the Kodi log', False)
    finally:
        if locked:
            _release()

