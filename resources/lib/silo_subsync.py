# -*- coding: utf-8 -*-
"""Silo's own subtitle work on a Silo source, as its apps do it (v5.10.486).

The server aligns a subtitle to the file's speech and stores a correction, and
translates subtitles with AI. Kodi only asks and loads the result:

  - with the server's auto_sync on, a subtitle is synced the first time it is
    served: run() follows that job and quietly loads the corrected copy;
  - the player's sync button on a Silo source opens a short menu: sync to the
    audio, restore the original timing, a search of the server's subtitle sites
    (SubDL, SubSource, OpenSubtitles), an AI translation, the best subtitle.

resolve_playback publishes the session's tracks (dexhub.silo.play); nothing
here runs or asks the server on any other source.
"""
import difflib
import json
import os
import re
import time

import xbmc
import xbmcgui
import xbmcvfs

PROP = 'dexhub.silo.play'
POLL = 3.0
SYNC_LIMIT = 5 * 60
AI_LIMIT = 30 * 60
AUTO_WAIT_FIRST = 8     # the server's own job (first time served) may take this to show
AUTO_WAIT = 40          # then one asked for here
ACTIVE = ('pending', 'running')
BITMAP = ('pgs', 'hdmv_pgs_subtitle', 'hdmv-pgs-subtitle', 'vobsub', 'dvd_subtitle', 'dvdsub', 'dvb_subtitle', 'xsub')
_FAILURES = {
    'subtitle_changed': ('الترجمة تغيرت أثناء المزامنة، جرّب مرة ثانية', 'The subtitle changed while syncing. Try again.'),
    'no_audio': ('هذا الفيديو ما فيه صوت يقدر السيرفر يقراه', 'This video has no audio the server can read.'),
    'unavailable': ('السيرفر مشغول، جرّب بعد شوي', 'The server is busy. Try again in a few minutes.'),
}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] silo subtitles: %s' % msg, level)


def _home():
    return xbmcgui.Window(10000)


def T(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _api(path, **kwargs):
    from .providers import silo_provider
    return silo_provider._api(path, **kwargs)


def publish(file_id, session_id, stream_url, subtitles):
    """The playing session's tracks, from resolve_playback (a plugin call): the
    service and the player's buttons are other processes. No request headers:
    they are added again when a track is read."""
    try:
        _label_ai_rows(file_id, subtitles)
        _mark_fit(file_id, subtitles)
        try:
            from .playback.subtitle_files import _safe_subtitle_filename_part, _subtitle_display_name
        except Exception:
            _safe_subtitle_filename_part = None
        tracks = []
        for row in subtitles or []:
            if not isinstance(row, dict):
                continue
            url = str(row.get('url') or '').split('|', 1)[0]
            tracks.append({'key': str(row.get('sync_key') or ''), 'url': url,
                           'lang': str(row.get('language') or row.get('lang') or ''),
                           'name': str(row.get('label') or row.get('name') or ''),
                           'index': row.get('combined_index'), 'source': str(row.get('source') or ''),
                           'codec': str(row.get('codec') or '').lower(),
                           'impaired': bool(row.get('hearing_impaired')), 'forced': bool(row.get('forced')),
                           # the start of the local file's name: Kodi names the
                           # track after the file, not after Silo's label
                           'file': (_safe_subtitle_filename_part(_subtitle_display_name(row), 60)
                                    if _safe_subtitle_filename_part else '')})
        try:
            # v5.10.494: before the player starts: a busy server is not waited for
            status = _api('subtitles/sync/status', retry=False) or {}
            auto = bool(status.get('allowed') and status.get('state') == 'available' and _auto_on())
        except Exception:
            auto = False
        _home().setProperty(PROP, json.dumps({'file_id': str(file_id), 'session': str(session_id or ''), 'auto': auto,
                                              'stream': str(stream_url or '').split('|', 1)[0].split('?', 1)[0],
                                              'tracks': tracks}))
    except Exception as exc:
        _log('publish: %s' % exc, xbmc.LOGWARNING)


def _mark_fit(file_id, subtitles):
    """The server's verdict on each subtitle against this file's audio, on its
    row (one read): the default subtitle is then a matched one when the same
    language has one, not a file timed for another release."""
    try:
        states = (_api('subtitles/%s/sync' % file_id, retry=False) or {}).get('subtitles') or []
    except Exception:
        return
    verdict = dict((s.get('key'), (s.get('sync') or {}).get('status')) for s in states if s.get('key'))
    for row in subtitles or []:
        if isinstance(row, dict) and verdict.get(row.get('sync_key')):
            row['sync_status'] = verdict[row['sync_key']]


def ai_label(kind, lang=''):
    """A Silo AI subtitle named by where it came from: the audio (heard, then
    translated) or another subtitle (translated). The server names both
    "English → Arabic (AI)"."""
    heard = str(kind or '').startswith('transcribe')
    word = T('AI من الصوت', 'AI from audio') if heard else T('AI من ترجمة', 'AI from a subtitle')
    return '%s · %s' % (word, lang) if lang else word


def _label_ai_rows(file_id, subtitles):
    """The session's stored AI subtitles get those names (one jobs read, only when one is there)."""
    rows = [r for r in subtitles or [] if isinstance(r, dict) and str(r.get('sync_key') or '').startswith('stored-')
            and '(AI' in str(r.get('label') or r.get('name') or '')]
    if not rows:
        return
    try:
        jobs = (_api('subtitles/ai/jobs', params={'media_file_id': file_id}, retry=False) or {}).get('jobs') or []
    except Exception:
        return
    kinds = dict((str(j.get('result_subtitle_id')), j.get('kind')) for j in jobs if j.get('result_subtitle_id'))
    for row in rows:
        stored = str(row.get('sync_key'))[len('stored-'):]
        label = str(row.get('label') or row.get('name') or '')
        kind = kinds.get(stored) or ('transcribe' if 'transcribed' in label.lower() else 'translate')
        name = ai_label(kind, str(row.get('language') or row.get('lang') or ''))
        row['label'] = row['name'] = row['title'] = name


def _state():
    """The published session, only while its video is the one playing."""
    raw = _home().getProperty(PROP)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        player = xbmc.Player()
        if not player.isPlayingVideo():
            return None
        playing = player.getPlayingFile()
    except Exception:
        return None
    if data.get('stream') and playing.split('|', 1)[0].split('?', 1)[0] == data['stream']:
        return data
    if data.get('session') and data['session'] in playing:
        return data
    return None


def leave_to_server(paths):
    """The Silo subtitle prepared for this playback: with the server's auto sync on,
    its timing is the server's; the local matcher leaves it (it renamed it, and the
    copy no longer counted as Silo's). The menu's local sync still can."""
    try:
        if not json.loads(_home().getProperty(PROP) or '{}').get('auto'):
            return
        from .subsync import autosync
        for path in paths or []:
            autosync.note_handled(path)
    except Exception as exc:
        _log('leave_to_server: %s' % exc, xbmc.LOGDEBUG)


def _norm(text):
    return re.sub(r'[\W_]+', '', str(text or '').lower())


def _base_lang(code):
    from .subsync import dexwatch
    return dexwatch.norm_lang(code) or str(code or '').lower()


def _stem(name):
    return _norm(re.sub(r'\.(srt|vtt|ass|ssa)$', '', str(name or ''), flags=re.I))


def current_track(state):
    """The session's track the player shows, or None: only a subtitle that came
    from this Silo session counts. Kodi names a track after its file, so the
    file Dex Hub wrote for it (or the name a corrected copy was loaded under)
    is compared, by language and the start of the name; the longest match wins.
    Another source's subtitle in the same language is not Silo's to sync."""
    from .subsync import dexwatch
    cur = dexwatch.current_subtitle()
    if not cur or not cur.get('enabled') or cur.get('index') is None or not cur.get('external'):
        return None         # off, or embedded: timed with the file
    want = _norm(cur.get('name'))
    shown = str(cur.get('name') or '').strip().lower()
    lang = cur.get('language') or ''
    best, best_len = None, 0
    for track in state.get('tracks') or []:
        if not track.get('key') or (lang and _base_lang(track.get('lang')) != lang):
            continue
        for name in (_stem(track.get('file')), _stem(track.get('name'))):
            size = min(len(name), len(want), 40)
            if size >= 12 and name[:size] == want[:size] and size > best_len:
                best, best_len = track, size
        # a download named "SubDL 1293": its whole name, then a word break
        short = str(track.get('name') or '').strip().lower()
        if short and re.match(r'%s(?![\w])' % re.escape(short), shown) and len(short) > best_len:
            best, best_len = track, len(short)
    return best


def _track_url(state, track):
    from .providers import silo_provider as S
    return track['url'] + '|' + S._stream_headers(S.account())


def _load(state, track, name=None, lang=None):
    """The track read again from the session (the server applies its timing), and shown."""
    from .playback.subtitle_files import SUBS_DIR, _download_remote_subtitle
    folder = os.path.join(SUBS_DIR, 'silo_%s' % state.get('file_id'))
    xbmcvfs.mkdirs(folder)
    stem = re.sub(r'[\\/:*?"<>|]+', ' ', str(name or track.get('name') or 'Silo')).strip()[:90]
    stem = re.sub(r'\.(srt|vtt|ass|ssa)$', '', stem, flags=re.I)
    # Kodi takes the subtitle's language from the first word of the name that
    # reads as one ("I Fought the Law" came out as Law): the code goes last, and
    # a provider download is named without its release words
    code = str(lang or track.get('lang') or '').split('-')[0].lower()
    code = ('.' + code) if re.match(r'^[a-z]{2,3}$', code) else ''
    path = os.path.join(folder, '%s.%d%s.vtt' % (stem, int(time.time()) % 100000, code))
    # the function answers with the real path: it renames a file to its real format
    path = _download_remote_subtitle(_track_url(state, track), path, language_hint=lang or track.get('lang') or '')
    if not path:
        raise IOError(T('السيرفر ما رجّع ملف الترجمة', "The server didn't return the subtitle file"))
    try:
        from .subsync import autosync
        autosync.note_handled(path)     # the audio's timing: the cue matcher leaves it
    except Exception:
        pass
    player = xbmc.Player()
    player.setSubtitles(path)
    player.showSubtitles(True)
    return path


def _describe(timing):
    timing = timing or {}
    scale = float(timing.get('scale') or 1)
    if abs(scale - 1.0) > 0.0005:
        return T('صحّحت فرق السرعة (%.2f%%)' % ((scale - 1) * 100), 'speed corrected (%.2f%%)' % ((scale - 1) * 100))
    sec = abs(int(timing.get('offset_ms') or 0)) / 1000.0
    if int(timing.get('offset_ms') or 0) < 0:
        return T('قدّمتها %.1f ث' % sec, 'moved %.1fs earlier' % sec)
    return T('أخّرتها %.1f ث' % sec, 'moved %.1fs later' % sec)


def _note(ar, en, ok=True, ms=5000):
    xbmcgui.Dialog().notification('Silo', T(ar, en),
                                  xbmcgui.NOTIFICATION_INFO if ok else xbmcgui.NOTIFICATION_WARNING, ms)


def _read(file_id, key):
    value = _api('subtitles/%s/sync/%s' % (file_id, key)) or {}
    return value.get('subtitle') or value


def sync_to_audio(state, track):
    """"Sync to Audio" of a Silo subtitle; the viewer picks the local sync from the menu."""
    from .subsync.osdflow import _Bar
    file_id, key = state['file_id'], track['key']
    bar = _Bar(T('أرسلت الترجمة للسيرفر…', 'Sent to the server…'))
    try:
        started = _api('subtitles/%s/sync/%s' % (file_id, key), method='POST', retry=False) or {}
        sub = started.get('subtitle') or started
        job = sub.get('sync') or {}
        limit = time.monotonic() + SYNC_LIMIT
        monitor = xbmc.Monitor()
        while (job.get('status') in ACTIVE) and time.monotonic() < limit:
            words = {'queued': ('بالدور على السيرفر…', 'Waiting to start…'),
                     'analyzing': ('يسمع صوت الفيديو…', 'Listening to the audio…'),
                     'matching': ('يطابق السطور مع الكلام…', 'Matching lines to speech…')}
            word = words.get(job.get('phase')) or words['queued']
            bar.update(float(job.get('progress') or 0) * 100, T(*word))
            if monitor.waitForAbort(POLL) or _state() is None:
                _log('sync of %s left running on the server' % key[:16])
                return True
            sub = _read(file_id, key)
            job = sub.get('sync') or {}
    finally:
        bar.close()
    status = job.get('status')
    pct = int(round(float(job.get('confidence') or 0) * 100))
    if status == 'synced':
        timing = job.get('result') or sub.get('timing')
        _load(state, track)
        _note('انضبطت على صوت الفيديو: %s (اتفاق %d%%)' % (_describe(timing), pct),
              'Synced to the audio: %s (%d%% agreement)' % (_describe(timing), pct))
        return True
    if status == 'already_synced':
        _note('الترجمة مطابقة للصوت أصلاً', 'Already matches the audio.')
        return True
    if status == 'no_match':
        _note('ما طابقت صوت هذا الفيديو، جرّب المزامنة المحلية', "Doesn't match this video's audio; try the local sync.", False)
        return False
    if status == 'failed':
        ar, en = _FAILURES.get(job.get('failure') or '', ('تعذرت المزامنة، جرّب مرة ثانية', 'Sync failed. Try again.'))
        _note(ar, en, False)
        return False
    _note('المزامنة طوّلت على السيرفر، تكمل هناك', 'The server is still syncing it.', False)
    return True


def reset_timing(state, track):
    from .providers import silo_provider as S
    file_id, key = state['file_id'], track['key']
    path = 'subtitles/%s/sync/%s' % (file_id, key)
    url = '%s/api/v2/%s' % (S._base(S.account().get('url')), path)
    _body, headers = S._raw(url, return_headers=True)
    etag = next((v for k, v in dict(headers or {}).items() if str(k).lower() == 'etag'), '')
    S._raw(url + '/timing', method='PUT', data={'offset_ms': 0, 'scale': 1}, retry=False,
           headers_extra={'If-Match': etag})
    _load(state, track)
    _note('رجعت للتوقيت الأصلي', 'Original timing restored')


# ---------------------------------------------------------------- AI
def _pref_lang():
    try:
        from .subsync.osdflow import _pref
        return _pref()[0] or 'ar'
    except Exception:
        return 'ar'


def _ai_sources(state, target):
    """The text tracks to translate from, the safest first. A track inside the file
    belongs to this video; a sidecar can be another title's (an English sidecar of
    one episode held a Korean drama, and the translation showed nothing). A sidecar
    the server found not to match the audio is left out."""
    try:
        states = (_api('subtitles/%s/sync' % state['file_id']) or {}).get('subtitles') or []
        bad = set(s.get('key') for s in states if (s.get('sync') or {}).get('status') == 'no_match')
    except Exception:
        bad = set()
    rows = [t for t in state.get('tracks') or [] if t.get('index') is not None and t.get('codec') not in BITMAP
            and not t.get('forced') and _base_lang(t.get('lang')) != _base_lang(target)
            and not (t.get('key') and t['key'] in bad)]

    def rank(t):
        return (0 if t.get('source') == 'embedded' else 1, 0 if _base_lang(t.get('lang')) == 'eng' else 1,
                1 if t.get('impaired') else 0)
    return sorted(rows, key=rank)


def _source_fits(state, source):
    """A sidecar is checked against the audio before it is translated: the server's
    answer on it, its sync asked for and waited on when there is none yet."""
    key = source.get('key')
    if not key:
        return True         # inside the file
    from .subsync.osdflow import _Bar
    file_id = state['file_id']
    sub = _read(file_id, key) or {}
    job = sub.get('sync') or {}
    if not job.get('status'):
        started = _api('subtitles/%s/sync/%s' % (file_id, key), method='POST', retry=False) or {}
        job = (started.get('subtitle') or started).get('sync') or {}
    bar = _Bar(T('أتأكد إن الترجمة لهذا الفيديو…', 'Checking the subtitle belongs to this video…'))
    limit = time.monotonic() + SYNC_LIMIT
    monitor = xbmc.Monitor()
    try:
        while job.get('status') in ACTIVE and time.monotonic() < limit:
            bar.update(float(job.get('progress') or 0) * 100, T('يسمع صوت الفيديو…', 'Listening to the audio…'))
            if monitor.waitForAbort(POLL) or _state() is None:
                return False
            job = (_read(file_id, key) or {}).get('sync') or {}
    finally:
        bar.close()
    return job.get('status') in ('synced', 'already_synced')


def _pick_source(sources):
    if len(sources) == 1:
        return sources[0]

    def label(t):
        where = T('داخل الفيديو', 'in the video') if t.get('source') == 'embedded' else T('ملف خارجي', 'external file')
        return '%s · %s · %s' % (t.get('lang') or '?', where, (t.get('name') or '')[:50])
    chosen = xbmcgui.Dialog().select(T('ترجم من أي ترجمة؟', 'Translate from which subtitle?'), [label(t) for t in sources[:12]])
    return sources[chosen] if chosen >= 0 else None


def _active_ai(file_id):
    try:
        for job in (_api('subtitles/ai/jobs', params={'media_file_id': file_id}) or {}).get('jobs') or []:
            if job.get('status') in ACTIVE:
                return job
    except Exception:
        pass
    return None


def _follow_ai(state, job):
    from .subsync.osdflow import _Bar
    bar = _Bar(T('السيرفر يترجم بالذكاء الاصطناعي…', 'The server is translating with AI…'))
    limit = time.monotonic() + AI_LIMIT
    monitor = xbmc.Monitor()
    try:
        while job.get('status') in ACTIVE and time.monotonic() < limit:
            bar.update(float(job.get('progress') or 0) * (100 if float(job.get('progress') or 0) <= 1 else 1),
                       str(job.get('progress_message') or T('يترجم…', 'Translating…')))
            if monitor.waitForAbort(5.0):
                return
            if _state() is None:
                _note('الترجمة تكمل على السيرفر، وبتلقاها في الترجمات', 'The translation continues on the server; it will be among the subtitles.')
                return
            job = (_api('subtitles/ai/jobs/%s' % job['id']) or {}).get('job') or job
    finally:
        bar.close()
    status = job.get('status')
    if status == 'completed' and job.get('result_subtitle_id'):
        name = ai_label(job.get('kind'), job.get('target_language') or '')
        track = _stored_track(state, job['result_subtitle_id'], name, job.get('target_language') or '')
        _load(state, track, name=name)
        _note('جاهزة: %s' % name, 'Ready: %s' % name)
    elif status == 'failed':
        _note(str(job.get('error_message') or 'AI'), str(job.get('error_message') or 'AI'), False, 8000)
    elif status == 'cancelled':
        _note('انلغت الترجمة', 'Translation cancelled', False)
    else:
        _note('الترجمة طوّلت، وتكمل على السيرفر', 'Still translating on the server.', False)


def _stored_track(state, stored_id, name, lang):
    """A subtitle stored on the server after the session began (an AI result, a
    provider download), read through the session like its own tracks, and added
    to the published tracks: the menu and the automatic sync then know it."""
    tracks = state.get('tracks') or []
    index = max([int(t['index']) for t in tracks if t.get('index') is not None] + [-1]) + 1
    base = next((t['url'] for t in tracks if '/subtitles/' in str(t.get('url'))), '')
    root = base.split('/subtitles/', 1)[0]
    if not root:
        # v5.10.494: a title that came with no subtitle (where a search or an
        # AI translation matters most): the session's own path, from its stream
        stream, session = str(state.get('stream') or ''), str(state.get('session') or '')
        if session and ('/%s/' % session) in stream:
            root = stream.split('/%s/' % session, 1)[0] + '/' + session
    if not root.startswith(('http://', 'https://')):
        _log('stored track %s: no session address to read it from' % stored_id, xbmc.LOGWARNING)
        raise IOError(T('الترجمة انحفظت في Silo، بس ما قدرت أحملها هنا. شغّل العمل من جديد وتطلع في الترجمات.',
                              'The subtitle is saved in Silo but could not be loaded here. Play the title again to find it in the subtitles.'))
    track = {'key': 'stored-%s' % stored_id, 'url': '%s/subtitles/%d.vtt?file_id=%s&downloaded_subtitle_id=%s' % (
        root, index, state['file_id'], stored_id), 'name': name, 'lang': lang, 'index': None, 'source': 'external'}
    try:
        data = json.loads(_home().getProperty(PROP) or '{}')
        if data.get('file_id') == state.get('file_id'):
            data['tracks'] = [t for t in data.get('tracks') or [] if t.get('key') != track['key']] + [track]
            _home().setProperty(PROP, json.dumps(data))
            state['tracks'] = data['tracks']
    except Exception as exc:
        _log('stored track: %s' % exc, xbmc.LOGDEBUG)
    return track


def request_ai(state, kind, source):
    """Sent once (the server's contract): an uncertain answer is read back from the jobs."""
    target = _pref_lang()
    body = {'media_file_id': state['file_id'], 'kind': kind, 'target_language': target,
            'source_index': int(source['index']) if source else -1,
            'source_language': (source or {}).get('lang') or '', 'start_position': 0.0}
    try:
        job = (_api('subtitles/ai/translate', method='POST', data=body, retry=False) or {}).get('job') or {}
    except Exception as exc:
        job = _active_ai(state['file_id']) or {}
        if not job:
            _note('ما قبل السيرفر الطلب: %s' % exc, 'The server refused: %s' % exc, False, 8000)
            return
    if job.get('id'):
        _log('AI job %s %s' % (job['id'], kind))
        _follow_ai(state, job)


# ---------------------------------------------------------------- provider search
PROVIDER_NAMES = {'subdl': 'SubDL', 'subsource': 'SubSource', 'opensubtitles': 'OpenSubtitles'}
LANG_NAMES = {'ar': ('العربية', 'Arabic'), 'en': ('الإنجليزية', 'English'), 'fr': ('الفرنسية', 'French'),
              'tr': ('التركية', 'Turkish'), 'es': ('الإسبانية', 'Spanish'), 'fa': ('الفارسية', 'Persian')}
_SXE = re.compile(r'(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)|(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)', re.I)
_SEASON = re.compile(r'(?<![a-z0-9])(?:s|season[ ._-]*)(\d{1,2})(?!\d)', re.I)


def _providers():
    """The search providers the server has on (SubDL, SubSource, OpenSubtitles), or none."""
    try:
        status = _api('subtitles/providers/status') or {}
    except Exception as exc:
        _log('providers: %s' % exc, xbmc.LOGDEBUG)
        return []
    if not status.get('enabled') or status.get('allowed') is False or status.get('state') not in (None, 'available'):
        return []
    return [str(p) for p in status.get('providers') or []]


def _episode():
    try:
        return int(xbmc.getInfoLabel('VideoPlayer.Season') or 0), int(xbmc.getInfoLabel('VideoPlayer.Episode') or 0)
    except ValueError:
        return 0, 0


def _fit(name, season, episode):
    """'ep' this episode, 'pack' its whole season, 'other' another one, '' can't tell (a film)."""
    if not episode:
        return ''
    m = _SXE.search(name or '')
    if m:
        s, e = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return 'ep' if (int(s), int(e)) == (season, episode) else 'other'
    m = _SEASON.search(name or '')
    if m:
        return 'pack' if int(m.group(1)) == season else 'other'
    return ''


def _lang_name(code):
    names = LANG_NAMES.get(_base_lang(code)[:2]) or LANG_NAMES.get(str(code or '')[:2])
    return T(*names) if names else str(code or '?')


def _search_rows(results, target, season, episode, stored):
    """The results in the order to choose from: the wanted language, this episode,
    then the server's score and how often each was downloaded. Another episode's
    subtitle goes to the end."""
    have = set((str(x.get('provider') or ''), str(x.get('release_name') or '')) for x in stored)
    rows = []
    for r in results or []:
        if not isinstance(r, dict) or not r.get('id') or not r.get('provider'):
            continue
        fit = _fit(r.get('release_name'), season, episode)
        rows.append(dict(r, fit=fit, mine=_base_lang(r.get('language')) == _base_lang(target),
                         saved=(str(r.get('provider')), str(r.get('release_name') or '')) in have))
    rank = {'ep': 0, '': 1, 'pack': 2, 'other': 3}
    rows.sort(key=lambda r: (r['fit'] == 'other', not r['mine'], rank[r['fit']],
                             -float(r.get('score') or 0), -int(r.get('downloads') or 0)))
    return rows


def _search_item(row):
    tags = [PROVIDER_NAMES.get(row['provider'], row['provider']), _lang_name(row.get('language'))]
    tags.append({'ep': T('✓ نفس الحلقة', '✓ this episode'), 'pack': T('حزمة الموسم', 'season pack'),
                 'other': T('⚠ حلقة ثانية', '⚠ another episode')}.get(row['fit'], ''))
    # the line is cut at the dialog's edge: what decides the choice comes first
    if row.get('saved'):
        tags.append(T('محفوظة في Silo', 'on Silo'))
    if row.get('hearing_impaired'):
        tags.append(T('للصم', 'HI'))
    if row.get('downloads'):
        tags.append('%s↓' % '{:,}'.format(int(row['downloads'])))
    item = xbmcgui.ListItem(label=str(row.get('release_name') or row['provider'])[:120],
                            label2=' · '.join(t for t in tags if t), offscreen=True)
    return item


def search_providers(state, can_sync, auto):
    """The server searches SubDL, SubSource and OpenSubtitles for this file, the
    viewer picks one, and the server keeps it with the file's subtitles; it plays
    from the session like the others, so the server's sync can time it."""
    target = _pref_lang()
    langs = [target] + ([] if _base_lang(target) == _base_lang('en') else ['en'])
    season, episode = _episode()
    bar = xbmcgui.DialogProgress()
    bar.create('Silo', T('يبحث في SubDL و SubSource و OpenSubtitles…', 'Searching SubDL, SubSource and OpenSubtitles…'))
    try:
        found = _api('subtitles/search', method='POST', data={'media_file_id': state['file_id'], 'languages': langs}) or {}
        try:
            stored = (_api('subtitles/%s' % state['file_id']) or {}).get('subtitles') or []
        except Exception:
            stored = []
    finally:
        bar.close()
    rows = _search_rows(found.get('results'), target, season, episode, stored)
    if found.get('warnings'):
        _note('بعض المواقع ما ردّت، النتائج ناقصة', "Some sites didn't answer; the list may be partial.", False)
    if not rows:
        _note('ما لقيت ترجمة لهذا الملف', 'No subtitles found for this file.', False)
        return
    only = any(r['mine'] for r in rows) and any(not r['mine'] for r in rows)
    flipped = False
    while True:
        view = [r for r in rows if r['mine']] if only else rows
        count = {}
        for r in view:
            name = PROVIDER_NAMES.get(r['provider'], r['provider'])
            count[name] = count.get(name, 0) + 1
        heading = T('ترجمات من Silo', 'Subtitles via Silo') + ' · ' + ' · '.join('%s %d' % kv for kv in sorted(count.items()))
        items = [_search_item(r) for r in view]
        toggle = None
        if any(not r['mine'] for r in rows):
            toggle = len(items)
            items.append(xbmcgui.ListItem(
                label=T('عرض %s فقط', 'Show %s only') % _lang_name(target) if not only else T('عرض كل اللغات', 'Show all languages'),
                label2=(T('%d نتيجة بكل اللغات', '%d results in all languages') % len(rows)) if only else
                (T('%d نتيجة بـ%s', '%d in %s') % (sum(1 for r in rows if r['mine']), _lang_name(target))), offscreen=True))
        pick = xbmcgui.Dialog().select(heading, items, preselect=toggle if flipped else 0, useDetails=True)
        flipped = False
        if pick < 0:
            return
        if pick == toggle:
            only, flipped = not only, True
            continue
        break
    row = view[pick]
    body = {'media_file_id': state['file_id'], 'provider': row['provider'], 'subtitle_id': str(row['id']),
            'language': row.get('language') or '', 'release_name': row.get('release_name') or '',
            'score': row.get('score') or 0, 'hearing_impaired': bool(row.get('hearing_impaired'))}
    bar = _bar_open(T('ينزّل الترجمة على السيرفر…', 'Downloading to the server…'))
    try:
        # sent once (the server's contract); a failure is shown, not retried
        sub = (_api('subtitles/download', method='POST', data=body, retry=False) or {}).get('subtitle') or {}
    finally:
        bar.close()
    if not sub.get('id'):
        raise IOError(T('السيرفر ما حفظ الترجمة', "The server didn't keep the subtitle"))
    lang = str(sub.get('language') or row.get('language') or '')
    site = PROVIDER_NAMES.get(row['provider'], row['provider'])
    name = '%s %s' % (site, sub['id'])
    track = _stored_track(state, sub['id'], name, lang)
    path = _load(state, track, name=name)
    name = '%s · %s' % (site, row.get('release_name') or lang)
    _log('downloaded %s %s as stored-%s' % (row['provider'], row['id'], sub['id']))
    if _base_lang(lang) == _base_lang('ar') and not _arabic_file(path):
        _note('نزلت، بس نصها مو عربي رغم إنها موسومة عربي. جرّب غيرها',
              'Downloaded, but its text is not Arabic though tagged Arabic. Try another.', False, 8000)
        return
    if can_sync and not auto:
        sync_to_audio(state, track)
    elif can_sync:
        _note('نزلت: %s · السيرفر يزامنها مع الصوت' % name, 'Downloaded: %s · the server syncs it to the audio' % name)
    else:
        _note('نزلت: %s' % name, 'Downloaded: %s' % name)


def _bar_open(msg):
    from .subsync.osdflow import _Bar
    return _Bar(msg)


def _arabic_file(path):
    try:
        from .subsync.dexpick import looks_arabic
        f = xbmcvfs.File(path)
        try:
            raw = f.readBytes()
        finally:
            f.close()
        return looks_arabic(bytes(raw))
    except Exception:
        return True


def _auto_on():
    """The viewer's one switch for automatic sync (the subtitle settings page):
    on, a Silo subtitle put on screen is synced by the server, asked for when
    the server's own auto sync didn't."""
    from .subsync import sync_prefs
    return sync_prefs.auto_on()


def _local_sync():
    from .subsync import buttons
    if buttons.dexsubtitles_script():
        xbmc.executebuiltin('RunScript(%s,sync)' % buttons.OTHER)
    else:
        from .subsync import osdflow
        osdflow.run(['sync'])


# ---------------------------------------------------------------- the button
def handle_button():
    """The player's sync button on a Silo source. False: not one, the usual flow runs."""
    state = _state()
    if state is None:
        return False
    try:
        sync = _api('subtitles/sync/status') or {}
        ai = _api('subtitles/ai/status') or {}
    except Exception as exc:
        _log('status: %s' % exc, xbmc.LOGWARNING)
        return False
    can_sync = sync.get('allowed') and sync.get('state') == 'available'
    can_ai = ai.get('allowed') and ai.get('state') == 'available'
    track = current_track(state) if can_sync else None
    sub = {}
    if track:
        try:
            sub = _read(state['file_id'], track['key']) or {}
        except Exception:
            sub = {}
    timing = sub.get('timing') or {}
    moved = bool(timing) and (int(timing.get('offset_ms') or 0) != 0 or float(timing.get('scale') or 1) != 1)
    job = _active_ai(state['file_id']) if can_ai else None
    target = _pref_lang()
    sources = _ai_sources(state, target) if can_ai and ai.get('enabled') else []
    rows = []
    if track:
        label = T('مزامنة مع الصوت (Silo)', 'Sync to audio (Silo)')
        status = (sub.get('sync') or {}).get('status')
        if status == 'no_match':
            label += T(' · ما طابقت قبل', " · didn't match before")
        elif moved:
            label += ' · ' + _describe(timing)
        rows.append((label, 'sync'))
        if moved:
            rows.append((T('رجّع التوقيت الأصلي', 'Restore original timing'), 'reset'))
    providers = _providers()
    if providers:
        rows.append((T('ابحث عن ترجمة (%s)', 'Search subtitles (%s)') % ' · '.join(
            PROVIDER_NAMES.get(p, p) for p in sorted(providers, key=lambda p: p != 'subdl')), 'search'))
    if job:
        rows.append((T('متابعة %s الشغالة', 'Follow the running %s') % ai_label(job.get('kind')), 'ai_follow'))
        rows.append((T('إلغاء ترجمة AI', 'Cancel the AI translation'), 'ai_cancel'))
    else:
        if sources:
            rows.append((T('AI من ترجمة موجودة ← %s (سريع، يحتاج ترجمة صحيحة)', 'AI from a subtitle → %s (quick, needs a right subtitle)') % target, 'ai_text'))
        if can_ai and ai.get('transcribe_enabled'):
            rows.append((T('AI من صوت الفيديو ← %s (أبطأ، يحسب من الحصة)', 'AI from the audio → %s (slower, uses the quota)') % target, 'ai_audio'))
    own = []
    if not track and can_sync:
        own = [t for t in state.get('tracks') or [] if t.get('key') and _base_lang(t.get('lang')) == _base_lang(target)]
        if own:
            rows.insert(0, (T('شغّل ترجمة من Silo (تتزامن مع الصوت)', 'Play a subtitle from Silo (synced to audio)'), 'own'))
    rows.append((T('أفضل ترجمة', 'Best subtitle'), 'best'))
    rows.append(((T('المزامنة التلقائية لكل الترجمات: شغالة', 'Auto sync, every subtitle: on') if _auto_on() else
                  T('المزامنة التلقائية لكل الترجمات: مطفية', 'Auto sync, every subtitle: off')), 'auto'))
    # a subtitle from anywhere: its timing checked here, on the file's own subtitle track
    try:
        from .subsync import dexwatch
        shown = dexwatch.current_subtitle() or {}
    except Exception:
        shown = {}
    if shown.get('enabled') and shown.get('external'):
        rows.insert(2 if moved and track else (1 if track else 0), (T('مزامنة محلية للترجمة الشغالة', 'Local sync of the playing subtitle'), 'local'))
    pick = xbmcgui.Dialog().select(T('ترجمة Silo', 'Silo subtitles'), [r[0] for r in rows])
    if pick < 0:
        return True
    action = rows[pick][1]
    try:
        if action == 'best':
            return False
        if action == 'auto':
            from .subsync import sync_prefs
            sync_prefs.set_auto(not _auto_on())
            _note('المزامنة التلقائية شغالة لكل الترجمات' if _auto_on() else 'المزامنة التلقائية مطفية',
                  'Auto sync is on for every subtitle' if _auto_on() else 'Auto sync is off')
            return True
        if action == 'sync':
            sync_to_audio(state, track)
        elif action == 'local':
            _local_sync()
        elif action == 'own':
            chosen = 0 if len(own) == 1 else xbmcgui.Dialog().select(T('ترجمات Silo', 'Silo subtitles'), [x.get('name') or x.get('lang') for x in own])
            if chosen >= 0:
                _load(state, own[chosen])
        elif action == 'reset':
            reset_timing(state, track)
        elif action == 'ai_text':
            source = _pick_source(sources)
            if source:
                if _source_fits(state, source):
                    request_ai(state, 'translate', source)
                else:
                    _note('الترجمة هذي ما تطابق صوت الفيديو، فما ترجمتها. جرّب غيرها أو AI من الصوت',
                          "That subtitle doesn't match this video's audio, so it wasn't translated. Try another or AI from the audio.", False, 8000)
        elif action == 'search':
            search_providers(state, can_sync, bool(sync.get('auto_sync')))
        elif action == 'ai_audio':
            request_ai(state, 'transcribe_translate', None)
        elif action == 'ai_follow':
            _follow_ai(state, job)
        elif action == 'ai_cancel':
            _api('subtitles/ai/jobs/%s/cancel' % job['id'], method='POST', retry=False)
            _note('انلغت الترجمة', 'Translation cancelled')
    except Exception as exc:
        _log('%s: %s' % (action, exc), xbmc.LOGWARNING)
        _note('ما قدرت أكمل: %s' % exc, "Couldn't finish: %s" % exc, False, 8000)
    return True


# ---------------------------------------------------------------- automatic
def _follow_auto(monitor, state, track):
    """A Silo subtitle put on screen is synced to the audio without a word: the
    server's job is followed (or asked for when there is none yet) and the
    corrected copy replaces it. When the server finds no match the local sync
    tries it, as for any other subtitle."""
    key = track['key']
    started = time.monotonic()
    asked = False
    while not monitor.abortRequested():
        if _state() is None or current_track(state) != track:
            return          # stopped, or another subtitle chosen meanwhile
        try:
            sub = _read(state['file_id'], key) or {}
        except Exception as exc:
            _log('read: %s' % exc, xbmc.LOGDEBUG)
            return
        job = sub.get('sync') or {}
        status = job.get('status')
        if status in ACTIVE:
            if time.monotonic() - started > SYNC_LIMIT:
                return
        elif status == 'synced':
            _load(state, track)
            _log('%s: synced on change (%s)' % (key[:16], sub.get('timing')))
            return
        elif status == 'already_synced':
            return
        elif status in ('no_match', 'failed'):
            _log('%s: server %s, local sync' % (key[:16], status))
            _local_fallback(state, track)
            return
        elif not asked and time.monotonic() - started > AUTO_WAIT_FIRST:
            # no job: the server syncs a subtitle the first time it is served,
            # and this one was served before (or auto sync missed it)
            asked = True
            try:
                _api('subtitles/%s/sync/%s' % (state['file_id'], key), method='POST', retry=False)
            except Exception as exc:
                _log('%s: sync request: %s' % (key[:16], exc), xbmc.LOGDEBUG)
                return
        elif asked and time.monotonic() - started > AUTO_WAIT:
            return
        if monitor.waitForAbort(3.0):
            return


def _local_fallback(state, track):
    """The track fetched again and handed to the local sync (the cue matcher in
    Dex Hub): it applies a corrected copy only when it is sure."""
    from .playback.subtitle_files import SUBS_DIR, _download_remote_subtitle
    from .subsync import autosync
    folder = os.path.join(SUBS_DIR, 'silo_%s' % state.get('file_id'))
    xbmcvfs.mkdirs(folder)
    stem = re.sub(r'[\\/:*?"<>|.]+', ' ', str(track.get('name') or 'Silo')).strip()[:60]
    path = _download_remote_subtitle(_track_url(state, track), os.path.join(folder, '%s local.vtt' % stem),
                                     language_hint=track.get('lang') or '')
    if path:
        autosync.enqueue(path, display_name=track.get('name') or '', origin='watch', quiet=True)


def run(monitor):
    """Service thread. No request is made until a Silo source plays."""
    shown = None
    while not monitor.waitForAbort(3.0):
        raw = _home().getProperty(PROP)
        if not raw:
            shown = None
            continue
        state = _state()
        if state is None:
            shown = None
            continue
        try:
            track = current_track(state)
        except Exception:
            track = None
        # every change of subtitle counts, a return to an earlier one too; the
        # corrected copy loaded here is the same track, so it doesn't count again
        now = (state.get('session'), track and track['key'])
        if now == shown:
            continue
        shown = now
        if not track:
            continue
        try:
            if not _auto_on():
                continue
            status = _api('subtitles/sync/status') or {}
            if status.get('allowed') and status.get('state') == 'available':
                _follow_auto(monitor, state, track)
        except Exception as exc:
            _log('auto: %s' % exc, xbmc.LOGDEBUG)
