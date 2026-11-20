# -*- coding: utf-8 -*-
"""Translating a subtitle (Dex Hub 5.10.588): the one playing, embedded or external.

Three services, picked in the subtitle list (osdflow.cmd_pick):

  google    Google Translate's free web address (no key, no account), the default
  ai        the viewer's own AI service from Control Centre › AI (ai_search: Groq,
            OpenAI, Gemini, Claude, Mistral, OpenRouter, DeepSeek, Grok, or any
            OpenAI-compatible address, a local one with no key included)
  dexworld  DexWorld's subtitle translation with the viewer's DexWorld code, the same
            contract Flix uses (Flix NUVIO.md, "DexWorld's translate contract")

Only the text goes out, a few hundred lines at a time; the timing stays as it is.
Each line is sent as one line (its line breaks joined) and laid out again for the
screen afterwards. A finished translation is kept in the add-on's profile, so the
same subtitle in the same language opens at once next time.

No Kodi import at the top: parse, layout and the Google batching are tested outside Kodi.
"""
import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import urlencode

GOOGLE_URL = 'https://translate.googleapis.com/translate_a/single?'
GOOGLE_CHARS = 4200        # one request (form body), well under the address's limit
AI_LINES = 60
DEXWORLD_LINES = 30        # its contract: up to 30 lines a batch, two at a time
WORKERS = {'google': 2, 'ai': 2, 'dexworld': 2}     # Google 2 (3 in 5.10.588): kinder to its limit
LINE_WIDTH = 42
SERVICES = ('google', 'ai', 'dexworld')

_TAG = re.compile(r'<[^>]{1,40}>')
_ASS_TAG = re.compile(r'\{[^}]*\}')
_POS = re.compile(r'^\s*\{\\an?\d\}')
_TIME = re.compile(r'(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})')
_ARROW = re.compile(r'^\s*([0-9:.,]+)\s*-->\s*([0-9:.,]+)')


class TranslateError(Exception):
    def __init__(self, reason, detail=''):
        Exception.__init__(self, '%s: %s' % (reason, detail) if detail else reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------- reading a subtitle
def _ms(stamp):
    m = _TIME.search(stamp)
    if not m:
        return None
    h, mi, s, frac = m.groups()
    frac = (frac + '00')[:3]
    return ((int(h or 0) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac)


def _ass_ms(stamp):
    try:
        h, m, s = stamp.strip().split(':')
        sec, cs = (s.split('.') + ['0'])[:2]
        return ((int(h) * 60 + int(m)) * 60 + int(sec)) * 1000 + int((cs + '00')[:2]) * 10
    except Exception:
        return None


def parse(text):
    """[(start ms, end ms, text)] of an SRT, WebVTT or ASS/SSA subtitle (already decoded)."""
    text = text.replace('\r\n', '\n').replace('\r', '\n').lstrip('﻿')
    if '[Events]' in text or text.lstrip().startswith('[Script Info]'):
        return _parse_ass(text)
    out = []
    for block in re.split(r'\n\s*\n', text):
        rows = [r for r in block.split('\n')]
        for i, row in enumerate(rows):
            m = _ARROW.match(row)
            if m:
                a, b = _ms(m.group(1)), _ms(m.group(2))
                body = '\n'.join(rows[i + 1:]).strip()
                if a is not None and b is not None and body:
                    out.append((a, b, body))
                break
    out.sort(key=lambda x: x[0])
    return out


def _parse_ass(text):
    out, fields = [], None
    in_events = False
    for row in text.split('\n'):
        s = row.strip()
        if s.startswith('['):
            in_events = s.lower() == '[events]'
            continue
        if not in_events:
            continue
        if s.lower().startswith('format:'):
            fields = [f.strip().lower() for f in s[7:].split(',')]
            continue
        if s.lower().startswith('dialogue:') and fields:
            parts = s[9:].split(',', len(fields) - 1)
            if len(parts) != len(fields):
                continue
            row_ = dict(zip(fields, parts))
            a, b = _ass_ms(row_.get('start', '')), _ass_ms(row_.get('end', ''))
            body = row_.get('text', '').replace('\\N', '\n').replace('\\n', '\n').replace('\\h', ' ').strip()
            if a is not None and b is not None and body:
                out.append((a, b, body))
    out.sort(key=lambda x: x[0])
    return out


# ---------------------------------------------------------------- one line, out and back
def flatten(text):
    """(position tag, the line as one line to translate, dialogue: whether it was '- a / - b')."""
    pos = ''
    m = _POS.match(text)
    if m:
        pos = m.group(0).strip()
    body = _ASS_TAG.sub('', _TAG.sub('', text))
    rows = [r.strip() for r in body.split('\n') if r.strip()]
    dialogue = len(rows) > 1 and all(r.startswith(('-', '‐', '–')) for r in rows)
    return pos, ' '.join(rows), dialogue


def layout(text, dialogue=False, width=LINE_WIDTH):
    """The translated line for the screen: two speakers on two lines, a long line cut in two."""
    text = re.sub(r'\s+', ' ', text or '').strip()
    if dialogue:
        parts = [p.strip() for p in re.split(r'\s+[-‐–]\s*(?=\S)', text) if p.strip()]
        if len(parts) >= 2:
            return '\n'.join(p if p.startswith(('-', '‐', '–')) else '- ' + p for p in parts)
    if len(text) <= width or ' ' not in text:
        return text
    middle = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == ' ']
    cut = min(spaces, key=lambda i: abs(i - middle))
    return text[:cut] + '\n' + text[cut + 1:]


def _srt_time(ms):
    ms = max(0, int(ms))
    return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def render_srt(lines):
    out = []
    for n, (a, b, text) in enumerate(lines, 1):
        out.append('%d\n%s --> %s\n%s\n' % (n, _srt_time(a), _srt_time(b), text))
    return '\n'.join(out)


# ---------------------------------------------------------------- the services
# v5.10.592: Google's free address answers 429 when an internet address asked it a lot (three
# devices and the tests on one home connection, 20 Nov); its second address (the browser
# extension's) has its own limit and takes each line as its own "q", so it is used for ten
# minutes after a 429
GOOGLE_ALT_URL = 'https://clients5.google.com/translate_a/t?'
GOOGLE_ALT_S = 600
_GOOGLE = {'alt_until': 0.0}
_FORM = {'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8', 'User-Agent': 'Mozilla/5.0'}


def _google_alt(texts, target, http_post):
    body = urlencode([('q', t) for t in texts]).encode('utf-8')
    url = GOOGLE_ALT_URL + urlencode({'client': 'dict-chrome-ex', 'sl': 'auto', 'tl': target})
    status, raw = http_post(url, body, _FORM)
    if status == 429:
        raise TranslateError('busy', 'google 429 (both addresses)')
    if status != 200:
        raise TranslateError('service', 'google alt %s' % status)
    try:
        data = json.loads(raw.decode('utf-8'))
        got = [(item[0] if isinstance(item, list) else item) for item in data]
        got = [str(g or '') for g in got]
    except Exception:
        raise TranslateError('service', 'google alt answer')
    if len(got) != len(texts):
        return None
    return [g.strip() for g in got]


def _google_batch(texts, target, http_post):
    if time.time() < _GOOGLE['alt_until']:
        return _google_alt(texts, target, http_post)
    body = urlencode({'q': '\n'.join(texts)}).encode('utf-8')
    url = GOOGLE_URL + urlencode({'client': 'gtx', 'sl': 'auto', 'tl': target, 'dt': 't'})
    status, raw = http_post(url, body, _FORM)
    if status == 429:
        _GOOGLE['alt_until'] = time.time() + GOOGLE_ALT_S
        return _google_alt(texts, target, http_post)
    if status != 200:
        raise TranslateError('service', 'google %s' % status)
    try:
        data = json.loads(raw.decode('utf-8'))
        joined = ''.join(seg[0] for seg in data[0] if seg and seg[0])
    except Exception:
        raise TranslateError('service', 'google answer')
    got = joined.split('\n')
    if len(got) != len(texts):
        return None
    return [g.strip() for g in got]


_AI_SYSTEM = ('You translate film and TV subtitles into %s. The user sends a JSON array of subtitle lines in order. '
              'Answer with only a JSON array of exactly the same length, each item the translation of the item at the '
              'same place: natural, short spoken %s as subtitles use it, names kept, no notes, no numbering, and no '
              'lines merged or split. Keep a leading "- " where a line has one.')


def _ai_batch(texts, target_name, chat):
    answer = chat(_AI_SYSTEM % (target_name, target_name), json.dumps(texts, ensure_ascii=False))
    if answer is None:
        raise TranslateError('service', 'ai')
    s = str(answer)
    a, b = s.find('['), s.rfind(']')
    if a < 0 or b <= a:
        return None
    try:
        got = json.loads(s[a:b + 1])
    except Exception:
        return None
    if not isinstance(got, list) or len(got) != len(texts):
        return None
    return [str(x or '').strip() for x in got]


def _dexworld_batch(texts, target, post):
    """DexWorld's translate contract (as Flix sends it): {"lines", "target", ...} in, {"lines"} out,
    one for one. ``post(lines, target)`` -> (status, decoded answer)."""
    status, answer = post(texts, target)
    if status in (429, 503):
        raise TranslateError('busy', 'dexworld %s' % status)
    if status in (401, 403):
        raise TranslateError('refused', _error_text(answer))
    if status != 200:
        raise TranslateError('service', 'dexworld %s %s' % (status, _error_text(answer)))
    got = answer.get('lines') if isinstance(answer, dict) else answer
    if not isinstance(got, list) or len(got) != len(texts):
        return None
    return [str(x or '').strip() for x in got]


def _error_text(answer):
    error = answer.get('error') if isinstance(answer, dict) else ''
    if isinstance(error, dict):
        error = error.get('message') or error.get('code') or ''
    return str(error or '')[:160]


def _chunks(items, service):
    """Lists of (index, text): by characters for Google, by lines for the AI and DexWorld."""
    out, cur, size = [], [], 0
    limit = DEXWORLD_LINES if service == 'dexworld' else AI_LINES
    for item in items:
        n = len(item[1]) + 1
        if cur and ((service == 'google' and size + n > GOOGLE_CHARS) or (service != 'google' and len(cur) >= limit)):
            out.append(cur)
            cur, size = [], 0
        cur.append(item)
        size += n
    if cur:
        out.append(cur)
    return out


def translate_texts(texts, service, target, call, target_name='', progress=None, cancelled=None, first=0):
    """The translations of ``texts`` (same length). ``call`` is the service's batch call:
    http_post(url, body, headers) -> (status, bytes) for Google, chat(system, user) -> text
    for the AI. Lines from index ``first`` (where the viewer is) go out first."""
    order = list(range(first, len(texts))) + list(range(0, first))
    todo = [(i, texts[i]) for i in order if texts[i].strip()]
    out = list(texts)
    queue = _chunks(todo, service)
    total = max(1, len(todo))
    state = {'done': 0, 'error': None}
    lock = threading.Lock()

    def run_batch(batch, depth=0):
        if cancelled and cancelled():
            raise TranslateError('cancelled')
        words = [t for _i, t in batch]
        for attempt in range(4):
            try:
                if service == 'google':
                    got = _google_batch(words, target, call)
                elif service == 'dexworld':
                    got = _dexworld_batch(words, target, call)
                else:
                    got = _ai_batch(words, target_name or target, call)
                break
            except TranslateError as e:
                if e.reason != 'busy' or attempt == 3:
                    raise
                time.sleep(2.0 * (attempt + 1))
        if got is None:                  # the lines came back merged or split: halve the batch
            if len(batch) == 1:
                got = [words[0]]
            else:
                half = len(batch) // 2
                run_batch(batch[:half], depth + 1)
                run_batch(batch[half:], depth + 1)
                return
        with lock:
            for (i, _t), g in zip(batch, got):
                out[i] = g or texts[i]
            state['done'] += len(batch)
            done = state['done']
        if progress:
            try:
                progress(done, total)
            except Exception:
                pass

    def worker():
        while True:
            with lock:
                if state['error'] or not queue:
                    return
                batch = queue.pop(0)
            try:
                run_batch(batch)
            except Exception as e:
                with lock:
                    state['error'] = state['error'] or e
                return

    threads = [threading.Thread(target=worker, name='dexhub-subtrans-%d' % n) for n in range(WORKERS.get(service, 2))]
    for th in threads:
        th.daemon = True
        th.start()
    for th in threads:
        th.join()
    if state['error']:
        e = state['error']
        raise e if isinstance(e, TranslateError) else TranslateError('error', '%s: %s' % (e.__class__.__name__, e))
    return out


def translate_lines(lines, service, target, call, target_name='', progress=None, cancelled=None, at_ms=0):
    """[(start, end, text)] -> the same lines with the text translated and laid out for the screen."""
    flat = [flatten(t) for _a, _b, t in lines]
    first = next((i for i, (a, b, _t) in enumerate(lines) if b >= at_ms), 0)
    got = translate_texts([f[1] for f in flat], service, target, call, target_name, progress, cancelled, first)
    out = []
    for (a, b, _t), (pos, _src, dialogue), g in zip(lines, flat, got):
        body = layout(g, dialogue)
        out.append((a, b, (pos + body) if pos else body))
    return out


def cache_key(lines, service, target, model=''):
    h = hashlib.sha1()
    h.update(('%s|%s|%s|' % (service, target, model)).encode('utf-8'))
    for a, b, t in lines:
        h.update(('%d|%d|%s\n' % (a, b, t)).encode('utf-8'))
    return h.hexdigest()[:20]


def cache_read(folder, key):
    path = os.path.join(folder, key + '.srt')
    try:
        with open(path, 'rb') as f:
            data = f.read()
        os.utime(path, None)
        return data
    except Exception:
        return None


def cache_write(folder, key, data, keep=60):
    try:
        if not os.path.isdir(folder):
            os.makedirs(folder)
        tmp = os.path.join(folder, key + '.tmp')
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, os.path.join(folder, key + '.srt'))
        files = sorted((os.path.join(folder, n) for n in os.listdir(folder) if n.endswith('.srt')),
                       key=lambda p: os.path.getmtime(p))
        for old in files[:-keep]:
            os.remove(old)
    except Exception:
        pass
