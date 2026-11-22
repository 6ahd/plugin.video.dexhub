# -*- coding: utf-8 -*-
"""The player's "Translate subtitle" (Dex Hub 5.10.588).

Pick a subtitle in the player (embedded or external), press Translate: Dex Hub reads its
lines (an external file, or an embedded MKV text track line by line from the file's index,
dextext), translates the text with Google Translate, the viewer's AI service or DexWorld (subtrans),
keeps the timing, and puts the result on as an external subtitle. A Silo source keeps the
Silo server's own AI translation (silo_subsync), as Silo's apps and Flix do.
"""
import io
import json
import os
import re
import time

import xbmc
import xbmcgui

from . import autosync
from . import dexcues
from . import dexwatch
from . import osdflow
from .osdflow import T, _note, _log, _Bar, _media, _streams, _stream_label, _pref, _apply, _current_file

NAMES = {'ar': ('العربية', 'Arabic'), 'en': ('الإنجليزية', 'English'), 'fr': ('الفرنسية', 'French'),
         'es': ('الإسبانية', 'Spanish'), 'de': ('الألمانية', 'German'), 'tr': ('التركية', 'Turkish'),
         'fa': ('الفارسية', 'Persian'), 'ur': ('الأردية', 'Urdu'), 'id': ('الإندونيسية', 'Indonesian'),
         'it': ('الإيطالية', 'Italian'), 'pt': ('البرتغالية', 'Portuguese'), 'ru': ('الروسية', 'Russian'),
         'hi': ('الهندية', 'Hindi'), 'ko': ('الكورية', 'Korean'), 'ja': ('اليابانية', 'Japanese'),
         'zh': ('الصينية', 'Chinese'), 'nl': ('الهولندية', 'Dutch'), 'he': ('العبرية', 'Hebrew')}

_WHY = {
    'image_track': ('هذي ترجمة صور من نوع ما ينقرا (VobSub أو DVB)، اختر ترجمة ثانية',
                    'This image subtitle (VobSub or DVB) cannot be read; choose another subtitle'),
    # v5.10.598: a PGS subtitle is read as text on the device (Control Centre › AI › Translate from PGS)
    'pgs_off': ('هذي ترجمة صور (PGS) و«ترجمة من PGS» مطفية في مركز التحكم › الذكاء الاصطناعي',
                'This is an image subtitle (PGS) and Translate from PGS is off in Control Centre › AI'),
    'no_vision': ('ترجمة الصور (PGS) تحتاج كود DexWorld، أو خدمة AI تقرا الصور مثل Gemini أو OpenRouter: من مركز التحكم › الذكاء الاصطناعي',
                  'An image subtitle (PGS) needs a DexWorld code, or an AI service that reads pictures such as Gemini or OpenRouter: Control Centre › AI'),
    'ocr_failed': ('خدمة AI ما قرت صور الترجمة، جرّب بعد شوي', 'The AI service did not read the subtitle pictures; try again shortly'),
    'ocr_unreadable': ('خدمة AI ردت بشي غير النص، جرّب نموذج ثاني لـ«ترجمة من PGS»',
                       'The AI service did not answer with the text; try another model for Translate from PGS'),
    'no_cues_for_track': ('فهرس الملف ما يقول وين سطور هذي الترجمة', "The file's index doesn't say where this subtitle's lines are"),
    'no_positions': ('فهرس الملف ما يقول وين سطور هذي الترجمة', "The file's index doesn't say where this subtitle's lines are"),
    'no_cues': ('الملف ما فيه فهرس، فما أقدر أقرأ ترجمته المدمجة', 'The file has no index, so its embedded subtitle cannot be read'),
    'not_mkv': ('الترجمة المدمجة تنقرى من ملفات MKV بس', 'Embedded subtitles are read from MKV files only'),
    'nomedia': ('ما أقدر أقرأ هذا البث', "This stream can't be read"),
    'no_range': ('الخادم ما يسمح بالقراءة الجزئية', 'The server has no range support'),
    'compressed_track': ('هذي الترجمة مضغوطة بطريقة ما ندعمها', 'This subtitle is compressed in a way Dex Hub does not read'),
    'no_file': ('ما لقيت ملف هذي الترجمة', "Couldn't find this subtitle's file"),
    'busy': ('خدمة الترجمة مشغولة، جرّب بعد شوي', 'The translation service is busy, try again shortly'),
    'service': ('خدمة الترجمة ما ردت', "The translation service didn't answer"),
    'empty': ('الترجمة فاضية', 'The subtitle is empty'),
    'refused': ('خدمة الترجمة رفضت المفتاح أو الكود', 'The translation service refused the key or code'),
    'http_io': ('خادم الفيديو ما رد على قراءة الترجمة، جرّب بعد شوي', 'The video server did not answer the subtitle reads; try again shortly'),
    'timeout': ('خادم الفيديو بطيء وما خلصت قراءة الترجمة، جرّب بعد شوي', 'The video server was too slow to read the subtitle; try again shortly'),
}


def _to(name_ar):
    """العربية -> للعربية"""
    return 'لل' + name_ar[2:] if name_ar.startswith('ال') else 'لـ' + name_ar


def _labels(pool):
    """The streams' labels, numbered where two read the same."""
    labels = [_stream_label(x) for x in pool]
    return ['%s · %d' % (lab, n + 1) if labels.count(lab) > 1 else lab for n, lab in enumerate(labels)]


def _why(reason):
    pair = _WHY.get(str(reason or ''))
    return T(*pair) if pair else T('ما قدرت أقرأ الترجمة الحين', "Couldn't read the subtitle now")


class _Stop(Exception):
    def __init__(self, reason, detail=''):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------- the service
# v5.10.588: chosen in Control Centre › AI › Subtitle translation (ai_search's 'subtitles' feature):
# Google Translate (the default), DexWorld's own translation, or a chat service with its model.
DEXWORLD_URL = 'https://dexworld.cc/subtitles/api'


def folder():
    import xbmcvfs
    return xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/subtrans/')


def _ai():
    from .. import ai_search
    return ai_search


def service():
    """'google', 'dexworld' or 'ai' (a chat service), falling back to Google when the one chosen is gone."""
    try:
        pid = _ai().feature('subtitles').get('service') or 'google'
    except Exception:
        return 'google'
    if pid == 'dexworld_tr':
        return 'dexworld' if dexworld_cfg() else 'google'
    if pid != 'google' and _ai_cfg():
        return 'ai'
    return 'google'


def dexworld_cfg():
    """{base, key, model} of DexWorld's /translate, from the DexWorld service and its code."""
    try:
        svc = _ai().services().get('dexworld')
    except Exception:
        svc = None
    if not svc or not svc.get('key'):
        return None
    base = str(svc.get('base') or '').rstrip('/')
    if base.endswith('/openai/v1'):
        base = base[:-len('/openai/v1')]
    return {'base': base or DEXWORLD_URL, 'key': svc['key'], 'model': ''}


def _ai_cfg():
    try:
        return _ai().config('subtitles')
    except Exception:
        return None


def service_label(which=None):
    which = which or service()
    if which == 'ai':
        cfg = _ai_cfg() or {}
        try:
            return '%s · %s' % (_ai().service_name(cfg.get('provider')), cfg.get('model') or '')
        except Exception:
            return 'AI'
    if which == 'dexworld':
        return 'DexWorld'
    return 'Google Translate'


def pick_service():
    """The subtitle translation's service and model (Control Centre › AI › Subtitle translation)."""
    return _ai().edit_feature('subtitles')


def _dexworld_post(cfg, lines, target, context):
    """One request of DexWorld's translate contract: (status, decoded answer)."""
    import urllib.request
    import urllib.error
    body = {'lines': lines, 'target': target, 'client': 'dexhub'}
    if cfg.get('model'):
        body['model'] = cfg['model']
    body.update(dict((k, v) for k, v in (context or {}).items() if v))
    headers = {'Content-Type': 'application/json', 'User-Agent': 'DexHub'}
    if cfg.get('key'):
        headers['Authorization'] = 'Bearer ' + cfg['key']
    request = urllib.request.Request(cfg['base'] + '/translate', data=json.dumps(body).encode('utf-8'), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45) as answer:
            return answer.status, json.loads(answer.read().decode('utf-8', 'replace') or '{}')
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode('utf-8', 'replace') or '{}')
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {'error': str(exc)[:160]}


def _title_context():
    """What plays, for DexWorld (it keeps translations by title): imdb_id, type, season, episode."""
    try:
        from ..subtitle_service import _playing_ctx
        ctx = _playing_ctx()
    except Exception:
        return {}
    series = ctx.get('media_type') == 'series'
    out = {'imdb_id': (ctx.get('show_imdb_id') if series else '') or ctx.get('imdb_id') or '',
           'type': 'series' if series else 'movie'}
    if series:
        out['season'] = str(ctx.get('season') or '')
        out['episode'] = str(ctx.get('episode') or '')
    return out


# ---------------------------------------------------------------- the subtitle's lines
def is_external(s):
    name = str(s.get('name') or '').strip().lower()
    return any(lab and name.endswith(lab.lower()) for lab in dexwatch._external_labels())


def _source(media, budget):
    kind = autosync._media_kind(media)
    if kind not in ('http', 'vfs'):
        raise _Stop('nomedia')
    base, headers = autosync._split_kodi_media_url(media)
    if kind == 'http':
        return dexcues.HttpSource(base, budget, autosync._SESSION.__class__(), headers=headers, verify=False)
    return dexcues.VfsSource(base, budget, autosync._vfs_open)


def _external_lines(media, pid, s, lang):
    from . import subtrans
    from ..subtitle_encoding import normalize
    playing = dexwatch.current_subtitle(pid) or {}
    if playing.get('index') == s.get('index'):
        cur = playing
    else:
        name = re.sub(r'\([^()]*\)\s*$', '', str(s.get('name') or '')).strip().lower()
        cur = {'index': s.get('index'), 'raw_name': s.get('name'), 'name': name, 'external': True, 'language': lang,
               'forced': bool(s.get('isforced')), 'tokens': [t for t in dexwatch._DELIMS.split(name) if t]}
    pick, _cands = _current_file(media, cur)
    if not pick:
        raise _Stop('no_file')
    raw = dexwatch._read_any(pick['path'], osdflow._session())
    if raw and raw[:2] == b'PK':
        raw = osdflow._unpack(raw)
    if not raw:
        raise _Stop('no_file')
    return subtrans.parse(normalize(raw, lang or '').decode('utf-8', 'replace'))


def _media_key(media, track_number, which, target, model):
    """The same embedded track translated before (same address, size of the read index): its
    translation opens without reading the lines again. Addresses with a changing token miss."""
    import hashlib
    base = autosync._split_kodi_media_url(media)[0]
    return hashlib.sha1(('%s#%s|%s|%s|%s' % (base, track_number, which, target, model)).encode('utf-8')).hexdigest()[:20]


def _embedded_track(media, subs, s, lang):
    from . import dextext
    embedded = [x for x in subs if not is_external(x)]
    k = next((n for n, x in enumerate(embedded) if x.get('index') == s.get('index')), -1)
    listing = dextext.text_tracks(_source(media, dexcues.Budget(dexcues.DEFAULTS)))
    if not listing.get('ok'):
        raise _Stop(listing.get('reason') or 'error', listing.get('detail') or '')
    tracks = listing['tracks']
    track = tracks[k] if 0 <= k < len(tracks) else None
    if track is None or (lang and track['lang'] and dexwatch.norm_lang(track['lang']) != lang):
        same = [t for t in tracks if dexwatch.norm_lang(t['lang']) == lang]   # Kodi's order is not the file's
        track = same[0] if len(same) == 1 else track
    if track is None:
        raise _Stop('no_cues_for_track')
    if not track['text']:
        from . import pgsocr
        if not pgsocr.is_pgs(track['codec']):
            raise _Stop('image_track')
        if not _ai().feature('ocr').get('on'):
            raise _Stop('pgs_off')
        cfg = _ai().vision_config()
        dexworld = bool(dexworld_cfg()) and bool(pgsocr.server_lang(_lang2(track['lang'] or lang or 'eng')))
        if not cfg and not dexworld:
            raise _Stop('no_vision')
        # v5.10.598: DexWorld reads it first (spec 1d); ``image`` is the device's own reader, if any
        track = dict(track, image=cfg or {}, dexworld=dexworld)
    return track


def _same_timing(en_text, ar_text):
    """True when a translation keeps its source's lines: most of its lines start with one of the
    source's (DexWorld's reading and its Arabic must be the same subtitle)."""
    from . import subtrans
    en = subtrans.parse(en_text or '')
    ar = subtrans.parse(ar_text or '')
    if not en or not ar or abs(len(ar) - len(en)) > max(5, len(en) // 10):
        return False
    starts = sorted(x[0] for x in en)
    import bisect
    near = 0
    for a, _b, _t in ar:
        i = bisect.bisect_left(starts, a - 60)
        if i < len(starts) and abs(starts[i] - a) <= 60:
            near += 1
    return near >= 0.8 * len(ar)


def _lang2(code):
    """eng -> en; '' for a language dexwatch does not know (its lang2 says 'ar' then)."""
    table = getattr(dexwatch, '_C2A', {})
    return table.get(dexwatch.norm_lang(code), '')


def _pgs_server(media, track, bar, stop, target):
    """v5.10.598 (spec 1d): a PGS subtitle read and translated by DexWorld, kept there for every
    subscriber with the same release. The device takes the pictures out of the file once a release
    (never a link), sends them, and waits. ('ar', srt text) when DexWorld translated it into Arabic,
    ('en', lines) when it read it only (no AI in the account: the device translates), or None when
    DexWorld cannot (the device reads the pictures itself then)."""
    import hashlib
    import shutil
    import urllib.parse
    import xbmcvfs
    from . import dextext, pgsocr, subtrans
    cfg = dexworld_cfg()
    context = _title_context()
    if not cfg or not context.get('imdb_id'):
        _log('translate: PGS: %s, the device reads it' % ('no DexWorld code' if not cfg else 'no IMDb id for this title'))
        return None
    lang = pgsocr.server_lang(_lang2(track['lang'] or 'eng'))
    parts = urllib.parse.urlsplit(cfg['base'])
    title = {'imdbId': context['imdb_id']}
    if context.get('type') == 'series' and context.get('season') and context.get('episode'):
        title.update(season=int(context['season']), episode=int(context['episode']))
    dw = pgsocr.DexWorld('%s://%s' % (parts.scheme, parts.netloc), cfg['key'], title)
    base = autosync._split_kodi_media_url(media)[0]
    fp_file = os.path.join(folder(), 'pgsfp-%s.json' % hashlib.sha1(('%s#%s' % (base, track['number'])).encode('utf-8')).hexdigest()[:20])
    state = {'sets': None}

    def read_sets():
        def step(done, total):
            bar.update(100.0 * done / max(1, total), '%s  %d / %d' % (T('أطلّع صور الترجمة من الملف…', 'Taking the subtitle pictures out of the file…'), done, total))
            _keep_lock()
        r = dextext.read_track(_source(media, dexcues.Budget(dexcues.DEFAULTS)), track['number'], workers=READERS,
                               progress=step, cancelled=stop, raw=True, window=pgsocr.WINDOW)
        if not r.get('ok'):
            raise _Stop(r.get('reason') or 'error', r.get('detail') or '')
        _log('translate: PGS: %d display sets read %s%s' % (len(r['lines']), dexcues.fmt_stats(r.get('stats')),
                                                          (', %d missed' % r['missed']) if r.get('missed') else ''))
        state['sets'] = r['lines'] if not r.get('missed') else None   # a gap would change the fingerprint
        return state['sets']

    fp = ''
    try:
        with open(fp_file, 'r') as f:
            fp = json.load(f).get('fp') or ''
    except Exception:
        pass
    # the index alone first: a release DexWorld already has needs no reading of the track at all
    ifp = ''
    try:
        starts = dextext.line_starts(_source(media, dexcues.Budget(dexcues.DEFAULTS)), track['number'])
        ifp = pgsocr.index_fingerprint(starts, lang) if starts else ''
    except Exception as e:
        _log('translate: PGS: index not read for the quick lookup: %s' % e)
    status, answer = 0, {}
    if ifp and not fp:
        status, answer = dw.lookup(ifp=ifp)
        _log('translate: PGS: DexWorld quick lookup %s %s' % (status, answer.get('status') if isinstance(answer, dict) else ''))
        if status == 200 and dw.known(answer) and answer.get('fp'):
            fp = answer['fp']
    if not (status == 200 and dw.known(answer)):
        if not fp:
            if not read_sets():
                return None
            fp = pgsocr.fingerprint(state['sets'], lang)
        status, answer = dw.lookup(fp, ifp)
        _log('translate: PGS: DexWorld lookup %s %s' % (status, answer.get('status') if isinstance(answer, dict) else ''))
    if fp:
        try:
            with open(fp_file, 'w') as f:
                json.dump({'fp': fp}, f)
        except Exception:
            pass
    if status != 200:
        return None
    if not fp:                                  # known by the index, but the server named no fp
        fp = answer.get('fp') or ''
        if not fp and answer.get('status') != 'ready':     # its status is asked by fp
            if not read_sets():
                return None
            fp = pgsocr.fingerprint(state['sets'], lang)
    sent, t0, en = False, time.time(), None
    names = {'queued': T('بالدور عند DexWorld…', 'Waiting its turn at DexWorld…'),
             'ocr': T('DexWorld يقرأ الصور…', 'DexWorld is reading the pictures…'),
             'translating': T('DexWorld يترجم…', 'DexWorld is translating…')}
    while True:
        st = answer.get('status')
        en = answer.get('en') or en
        if st == 'ready' or (st == 'translating' and target != 'ar' and en):
            if target == 'ar' and answer.get('ar'):
                if _same_timing(en, answer['ar']):
                    _log('translate: PGS: Arabic from DexWorld (%s lines)' % answer.get('cues'))
                    return 'ar', answer['ar']
                # 21 Nov: DexWorld once gave another release's translation of the episode
                _log('translate: PGS: DexWorld\'s Arabic is not this subtitle\'s (other timing), translated here',
                     xbmc.LOGWARNING)
                answer = dict(answer, ar='', translate='failed')
            if target != 'ar' or answer.get('translate') in ('off', 'failed', 'done', None):
                lines = subtrans.parse(en or '')
                _log('translate: PGS: English from DexWorld (%d lines), translated here' % len(lines))
                return ('en', lines) if len(lines) >= 2 else None
        elif st in ('missing', 'failed') and not sent:
            sets = state['sets'] or read_sets()
            if not sets:
                return None
            fp = fp or pgsocr.fingerprint(sets, lang)
            shots = pgsocr.fit_for_server(pgsocr.pictures(sets))
            if not shots:
                _log('translate: PGS: %d pictures do not fit DexWorld, the device reads them' % len(sets))
                return None
            temp = os.path.join(xbmcvfs.translatePath('special://temp/'), 'dexhub-pgs', 'send-%d' % int(time.time() * 1000))
            try:                                # the pictures stay on the device only until they are sent
                os.makedirs(temp)
                for n, (start, _e, data) in enumerate(shots):
                    with open(os.path.join(temp, '%05d_%08d.png' % (n, start)), 'wb') as f:
                        f.write(data)
                bar.update(0, T('أرسل %d صورة لـ DexWorld…' % len(shots), 'Sending %d pictures to DexWorld…' % len(shots)))
                status, answer = dw.submit(fp, lang, shots, ifp=ifp)
            finally:
                shutil.rmtree(temp, ignore_errors=True)
            _log('translate: PGS: sent %d pictures (%d KB): %s %s' % (len(shots), sum(len(x[2]) for x in shots) // 1024, status,
                                                                     answer.get('status') or answer.get('error') or ''))
            if status not in (200, 201, 202):
                return None                         # busy (503), over a limit...: the device reads it
            sent = True
            continue
        elif st == 'failed':
            _log('translate: PGS: DexWorld could not read it: %s' % answer.get('error'), xbmc.LOGWARNING)
            return ('en', subtrans.parse(en)) if en else None
        if stop() or time.time() - t0 > pgsocr.POLL_LIMIT_S:
            _log('translate: PGS: DexWorld not done in %ds' % pgsocr.POLL_LIMIT_S, xbmc.LOGWARNING)
            if en:
                return 'en', subtrans.parse(en)
            if stop():
                raise _Stop('cancelled')
            return None
        pct = answer.get('progress')
        msg = names.get(st, T('أنتظر DexWorld…', 'Waiting for DexWorld…'))
        if isinstance(pct, str) and pct.strip():
            msg = 'DexWorld: %s' % pct.strip()
        bar.update(float(pct) if isinstance(pct, (int, float)) else min(95, (time.time() - t0) * 2), msg)
        _keep_lock()
        time.sleep(pgsocr.POLL_S)
        status, answer = dw.status(fp)
        if status != 200 or not isinstance(answer, dict):
            answer = {'status': 'queued'}


def _pgs_lines(media, track, bar, stop, want):
    """v5.10.598: a PGS track's lines, read as text from its pictures (subsync/pgsocr.py); the
    pictures go to a temp folder of their own and are deleted once read."""
    import hashlib
    import xbmcvfs
    from . import pgsocr
    cfg = track['image']
    names = {'read': T('أقرأ ترجمة الصور…', 'Reading the image subtitle…'),
             'ocr': T('أطلّع النص من الصور (%s)…' % service_label_of(cfg), 'Reading the text in the pictures (%s)…' % service_label_of(cfg))}

    def step(stage, done, total):
        bar.update(100.0 * done / max(1, total), '%s  %d / %d' % (names[stage], done, total))
        _keep_lock()

    def chat(system, parts):
        return _ai()._chat(cfg, system, parts, timeout=90)[0]
    base = autosync._split_kodi_media_url(media)[0]
    key = hashlib.sha1(('%s#%s|%s' % (base, track['number'], cfg.get('model') or '')).encode('utf-8')).hexdigest()[:20]
    temp = os.path.join(xbmcvfs.translatePath('special://temp/'), 'dexhub-pgs', '%d' % int(time.time() * 1000))
    try:
        return pgsocr.lines(_source(media, dexcues.Budget(dexcues.DEFAULTS)), track['number'], chat, temp,
                            cache_dir=folder(), key=key, want=want, workers=READERS, progress=step, cancelled=stop,
                            log=lambda m: _log('translate: ' + m))
    except pgsocr.OcrError as e:
        _log('translate: PGS pictures not read: %s %s' % (e.reason, e.detail), xbmc.LOGWARNING)
        raise _Stop(e.reason, e.detail)


def service_label_of(cfg):
    try:
        return '%s · %s' % (_ai().service_name(cfg.get('provider')), cfg.get('model') or '')
    except Exception:
        return 'AI'


def _embedded_lines(media, track, bar, stop, want=None, label=''):
    from . import dextext
    if track.get('image') or track.get('dexworld'):
        if not track.get('image'):
            raise _Stop('no_vision')
        return _pgs_lines(media, track, bar, stop, want)

    def step(done, total):
        bar.update(100.0 * done / max(1, total), '%s  %d / %d' % (label, done, total))
        _keep_lock()
    # 12 readers at a time: a CDN answers each small read in about 0.8 s (an Emby stream on box .54,
    # 19 Nov: 971 lines took 126 s with 6)
    r = dextext.read_track(_source(media, dexcues.Budget(dexcues.DEFAULTS)), track['number'], workers=READERS,
                           progress=step, cancelled=stop, want=want)
    if not r.get('ok'):
        _log('translate: embedded track %s not read: %s %s' % (track['number'], r.get('reason'),
                                                                dexcues.fmt_stats(r.get('stats'))), xbmc.LOGWARNING)
        raise _Stop(r.get('reason') or 'error', r.get('detail') or '')
    _log('translate: embedded track %s (%s, %s) %d lines %s%s' % (
        track['number'], track['codec'], track['lang'], len(r['lines']), dexcues.fmt_stats(r.get('stats')),
        (', %d not given by the server' % r['missed']) if r.get('missed') else ''))
    return r['lines']


# v5.10.590: a new Translate subtitle while one still runs (its rest in the background, often) stops
# that one and starts at once; before, it said "still working on the previous request" and did
# nothing, which read as "it cannot translate" (box .54, 20 Nov)
_RUN_PROP = 'dexhub.subtrans.run'
_STOP_PROP = 'dexhub.subtrans.stop'
TAKEOVER_S = 10


def _claim():
    token = '%.6f' % time.time()
    try:
        xbmcgui.Window(10000).setProperty(_RUN_PROP, token)
    except Exception:
        pass
    return token


def _replaced(token):
    try:
        return xbmcgui.Window(10000).getProperty(_STOP_PROP) == token
    except Exception:
        return False


def take_over():
    """A translation runs: ask it to stop and wait for its lock (osdflow), at most TAKEOVER_S.
    True once the lock is free."""
    w = xbmcgui.Window(10000)
    running = w.getProperty(_RUN_PROP)
    if not running:
        return False
    w.setProperty(_STOP_PROP, running)
    monitor = xbmc.Monitor()
    end = time.time() + TAKEOVER_S
    while time.time() < end:
        if not w.getProperty(osdflow._BUSY_PROP):
            return True
        if monitor.waitForAbort(0.2):
            return False
    return False


def _keep_lock():
    """The button lock lasts two minutes; a long translation keeps it."""
    try:
        xbmcgui.Window(10000).setProperty(osdflow._BUSY_PROP, str(time.time()))
    except Exception:
        pass


# ---------------------------------------------------------------- the button
def _http_post(url, body, headers):
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers), timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b''
    except Exception:
        return 0, b''


def _chosen():
    try:
        return _ai().feature('subtitles').get('service') or 'google'
    except Exception:
        return 'google'


def _silo_first():
    """Silo chosen: the server's AI translation of this video's subtitle (v5.10.590)."""
    try:
        from .. import silo_subsync
        return silo_subsync.translate()
    except Exception as exc:
        _log('silo subtitles: %s' % exc, xbmc.LOGWARNING)
        return False


# v5.10.590: a smaller first part, then the next part, then the rest, counted in lines so a quiet
# scene still has words in the first part (an embedded line is one request; box .54's Emby stream
# answered each in about a second, so 5.10.588's first five minutes, 138 lines, took 14 s to show)
FIRST_BACK_MS = 10 * 1000          # the part shown first: from ten seconds back...
FIRST_LINES = 48                   # ...the next 48 lines
NEXT_LINES = 320                   # embedded: the 320 lines after them, then the rest
READERS = 12                       # embedded lines read at a time (16 made box .54's Emby stream refuse reads)
FIRST_DEADLINE = 25                # seconds: nothing on the screen by then, the translation is cancelled
FIRST_DEADLINE_PGS = 60            # v5.10.598: a PGS subtitle's pictures are read as text first
TOTAL_DEADLINE = 10 * 60           # seconds for the rest of the subtitle


def _file_for(name, target):
    """One fixed file per source and service, so the finished translation replaces the first part."""
    base = re.sub(r'[^\w\-. ]+', ' ', name).strip(' .-_') or 'Dex Hub'
    if not os.path.isdir(autosync.WATCH_DIR):
        os.makedirs(autosync.WATCH_DIR)
    return os.path.join(autosync.WATCH_DIR, '%s.%s.srt' % (base, target)), base


def _write(path, data):
    tmp = path + '.tmp'
    with io.open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def _put_on(path, data, media):
    _write(path, data)
    p = xbmc.Player()
    p.setSubtitles(path)
    p.showSubtitles(True)
    autosync.claim_latest(data, media)          # an older AutoSync job does not land on top of it
    autosync.note_handled(path, is_ai=False)


def _reload(path, data, base, away_index):
    """The finished file in place of the first part: Kodi reads a subtitle file when the
    stream is opened, so the viewer's stream steps to the source one and back. A viewer
    who picked another subtitle meanwhile keeps it (the file is ready when they come back)."""
    _write(path, data)
    pid, subs, cur, enabled = _streams()
    ours = next((x for x in subs if str(x.get('name') or '').startswith(base)), None)
    if pid is None or ours is None or not enabled or cur.get('index') != ours.get('index'):
        return False
    away = away_index if any(x.get('index') == away_index for x in subs) and away_index != ours.get('index') else None
    if away is None:
        away = next((x.get('index') for x in subs if x.get('index') != ours.get('index')), None)
    if away is None:
        return False
    dexwatch._jsonrpc('Player.SetSubtitle', {'playerid': pid, 'subtitle': away, 'enable': True})
    xbmc.sleep(150)
    dexwatch._jsonrpc('Player.SetSubtitle', {'playerid': pid, 'subtitle': ours.get('index'), 'enable': True})
    return True


def _span(starts, at_ms, per_line=1):
    """{lo, one, two}: the first part from ``lo`` to ``one`` (FIRST_LINES lines), the next to ``two``.
    ``per_line``: index entries a line (2 for PGS: the line, then the set that clears it)."""
    lo = at_ms - FIRST_BACK_MS
    after = [t for t in starts if t >= lo]
    if not after:                      # past the last line: the last lines first
        return {'lo': starts[max(0, len(starts) - FIRST_LINES * per_line)] if starts else 0, 'one': float('inf'),
                'two': float('inf')}

    def upto(n):
        return after[min(n * per_line, len(after)) - 1]
    return {'lo': lo, 'one': upto(FIRST_LINES), 'two': upto(FIRST_LINES + NEXT_LINES)}


def run():
    from . import subtrans
    from . import dextext
    media = _media()
    if not media:
        _note('شغّل الفيديو أول', 'Play a video first', False)
        return
    # v5.10.590: Silo is a choice in the list (under Google and AI), no longer taken first on a
    # Silo title; with another service chosen, the subtitle on screen is translated here
    if _chosen() == 'silo':
        if _silo_first():
            return
        _log('translate: Silo chosen, but no Silo source plays: Google Translate')
    pid, subs, cur, enabled = _streams()
    target, target3 = _pref()
    tname_ar, tname_en = NAMES.get(target, (target, dexwatch.lang_name(target3)))
    s = next((x for x in subs if enabled and x.get('index') == cur.get('index')), None)
    if s is None or dexwatch.norm_lang(s.get('language')) == target3:
        pool = [x for x in subs if dexwatch.norm_lang(x.get('language')) != target3]
        if not pool:
            _note('ما فيه ترجمة بلغة ثانية أترجمها', 'There is no subtitle in another language to translate', False)
            return
        idx = xbmcgui.Dialog().select(T('أي ترجمة أترجمها %s؟' % _to(tname_ar), 'Which subtitle into %s?' % tname_en),
                                      _labels(pool))
        if idx < 0:
            return
        s = pool[idx]
    which = service()
    cfg = _ai_cfg() if which == 'ai' else (dexworld_cfg() if which == 'dexworld' else None)
    label = service_label(which)
    model = (cfg or {}).get('model') or ''
    lang = dexwatch.norm_lang(s.get('language'))
    try:
        at_ms = int(xbmc.Player().getTime() * 1000)
    except Exception:
        at_ms = 0
    t0 = time.time()
    state = {'shown': False, 'first': FIRST_DEADLINE}
    token = _claim()

    def gone():
        return _media() != media or xbmc.Monitor().abortRequested()

    def late():
        return time.time() > t0 + (TOTAL_DEADLINE if state['shown'] else state['first'])

    stop = lambda: gone() or late() or _replaced(token)

    span = {'lo': at_ms - FIRST_BACK_MS, 'one': at_ms, 'two': at_ms}

    def window(ms):
        return span['lo'] <= ms <= span['one']

    def ahead(ms):
        return span['one'] < ms <= span['two']

    def later(ms):
        return not (span['lo'] <= ms <= span['two'])

    if which == 'google':
        call = _http_post
    elif which == 'dexworld':
        context = _title_context()

        def call(lines_, target_):
            return _dexworld_post(cfg, lines_, target_, context)
    else:
        from .. import ai_search

        def call(system, user):
            return ai_search._chat(cfg, system, user, timeout=90)[0]

    bar = _Bar(T('أجهّز الترجمة…', 'Getting the subtitle ready…'))
    first = []
    total = 0
    try:
        try:
            all_lines = media_key = track = None
            if is_external(s):
                all_lines = _external_lines(media, pid, s, lang)
                if len(all_lines) < 2:
                    raise _Stop('empty')
                key = subtrans.cache_key(all_lines, which, target, model)
            else:
                track = _embedded_track(media, subs, s, lang)
                lang = lang or dexwatch.norm_lang(track['lang'])
                if track.get('dexworld'):
                    state['first'] = 8 * 60          # the pictures out of the file, then DexWorld's reading
                elif track.get('image'):
                    state['first'] = FIRST_DEADLINE_PGS
                key = media_key = _media_key(media, track['number'], which, target, model)
            src = NAMES.get(dexwatch.lang2(lang), (lang, lang or 'Sub'))[1]
            path, base = _file_for('Dex Hub %s %s' % (src, {'google': 'Google', 'dexworld': 'DexWorld'}.get(which, 'AI')),
                                   target)
            cached = subtrans.cache_read(folder(), key)
            if cached is not None:                 # translated before: on at once
                _put_on(path, cached, media)
                total = len(subtrans.parse(cached.decode('utf-8-sig', 'replace')))
                _log('translate: %d lines from the cache' % total)
                _note('ترجمتها %s بـ %s (%d سطر)' % (_to(tname_ar), label, total),
                      'Translated into %s with %s (%d lines)' % (tname_en, label, total))
                return
            if track is not None and track.get('dexworld'):
                got = _pgs_server(media, track, bar, stop, target)
                if got and got[0] == 'ar':
                    data = b'\xef\xbb\xbf' + got[1].lstrip('\ufeff').encode('utf-8')
                    subtrans.cache_write(folder(), key, data)
                    _put_on(path, data, media)
                    total = len(subtrans.parse(got[1]))
                    _log('translate: PGS: %d lines from DexWorld in %.1fs' % (total, time.time() - t0))
                    _note('ترجمتها %s من DexWorld (%d سطر)' % (_to(tname_ar), total),
                          'Translated into %s by DexWorld (%d lines)' % (tname_en, total))
                    return
                if got:
                    all_lines = got[1]               # DexWorld read it; translated here like a text subtitle
                    state['first'] = TOTAL_DEADLINE
                    t0 = time.time()
                elif not track.get('image'):
                    raise _Stop('no_vision')

            # 1. the lines around the viewer, on the screen as soon as they are translated
            span.update(_span(sorted(x[0] for x in all_lines) if all_lines is not None else
                              dextext.line_starts(_source(media, dexcues.Budget(dexcues.DEFAULTS)), track['number']),
                              at_ms, 2 if track and track.get('image') else 1))
            reading = T('أقرأ الترجمة…', 'Reading the subtitle…')
            if all_lines is not None:
                first = [x for x in all_lines if window(x[0])]
            else:
                first = _embedded_lines(media, track, bar, stop, want=window, label=reading)

            def step(done, n):
                bar.update(100.0 * done / max(1, n), '%s  %d / %d' % (T('أترجم بـ %s…' % label, 'Translating with %s…' % label), done, n))
                _keep_lock()
            out1 = subtrans.translate_lines(first, which, target, call, tname_en, step, stop, at_ms) if first else []
            if late():                         # a request that ran past the limit still cancels it
                raise _Stop('cancelled')
            if out1:
                _put_on(path, b'\xef\xbb\xbf' + subtrans.render_srt(out1).encode('utf-8'), media)
                state['shown'] = True
                _log('translate: first part (%d lines) on in %.1fs' % (len(out1), time.time() - t0))
                _note('الترجمة شغالة، والباقي يكمل بالخلفية', 'The translation is on; the rest follows in the background', True, 3000)

            # 2. embedded: the next minutes (on in place of the first part), then the rest; the
            # lines of an external file are all known already, so the rest goes in one part
            out2, rest = [], []
            if all_lines is not None:
                parts = [lambda ms: not window(ms)]
            else:
                parts = [ahead, later]
            for n, inside in enumerate(parts):
                if all_lines is not None:
                    part = [x for x in all_lines if inside(x[0])]
                else:
                    part = _embedded_lines(media, track, bar, stop, want=inside, label=reading)
                out = subtrans.translate_lines(part, which, target, call, tname_en, step, stop, 0) if part else []
                rest += part
                out2 += out
                if out and n < len(parts) - 1:
                    data = b'\xef\xbb\xbf' + subtrans.render_srt(sorted(out1 + out2, key=lambda x: x[0])).encode('utf-8')
                    if state['shown']:
                        _reload(path, data, base, s.get('index'))
                    else:
                        _put_on(path, data, media)
                        state['shown'] = True
                    _log('translate: the next minutes on too (%d lines) in %.1fs' % (len(out1) + len(out2),
                                                                                    time.time() - t0))
            full = sorted(out1 + out2, key=lambda x: x[0])
            if len(full) < 2:
                raise _Stop('empty')
            data = b'\xef\xbb\xbf' + subtrans.render_srt(full).encode('utf-8')
            subtrans.cache_write(folder(), key, data)
            if all_lines is None:
                subtrans.cache_write(folder(), subtrans.cache_key(sorted(first + rest), which, target, model), data)
            total = len(full)
            if state['shown']:
                _reload(path, data, base, s.get('index'))
            else:
                _put_on(path, data, media)
                state['shown'] = True
        except (_Stop, dexcues.CuesError, subtrans.TranslateError) as e:
            if gone():
                return
            if _replaced(token):
                _log('translate: stopped for a new request')
                return
            if e.reason == 'cancelled' or late():
                if state['shown']:
                    _log('translate: the rest took longer than %d min, stopped' % (TOTAL_DEADLINE // 60), xbmc.LOGWARNING)
                    _note('ترجمت الدقائق الجاية بس، الباقي طوّل فوقفته', 'Only the next minutes are translated; the rest took too long and was stopped',
                          False, 6000)
                else:
                    _log('translate: nothing ready in %ds, cancelled' % state['first'], xbmc.LOGWARNING)
                    _note('الترجمة ما جهزت خلال %d ثانية، فلغيتها' % state['first'],
                          'The translation was not ready within %d seconds, so it was cancelled' % state['first'], False, 6000)
                return
            _log('translate: %s %s' % (e.reason, getattr(e, 'detail', '')), xbmc.LOGWARNING)
            if state['shown']:
                # v5.10.590: the part on the screen stays; say plainly why the rest is missing
                _note('ترجمت جزء منها بس: خادم الفيديو ما رد على الباقي، جرّب بعد شوي',
                      'Only part of it is translated: the video server did not answer for the rest; try again shortly',
                      False, 6000)
                return
            _note(_why(e.reason), _why(e.reason), False, 6000)
            return
    finally:
        bar.close()
        try:
            w = xbmcgui.Window(10000)
            if w.getProperty(_RUN_PROP) == token:
                w.clearProperty(_RUN_PROP)
            if w.getProperty(_STOP_PROP) == token:
                w.clearProperty(_STOP_PROP)
        except Exception:
            pass
    _log('translate: %d lines with %s in %.1fs' % (total, which, time.time() - t0))
    _note('ترجمتها كاملة %s بـ %s (%d سطر)' % (_to(tname_ar), label, total),
          'All of it translated into %s with %s (%d lines)' % (tname_en, label, total))
