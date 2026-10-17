# -*- coding: utf-8 -*-
"""The player's subtitle page and audio page (v5.10.316), one design for both.

Subtitles:
  الترجمة              the subtitle on screen; OK opens the side page with
                       every track (Left and Right step through them)
  ترجمات من الإضافات   OK opens the side page and asks the subtitle add-ons
                       (the Stremio broker, as Kodi's subtitle search did);
                       a result is downloaded and shown at once
  إظهار، التأخير، الرفع والخفض، المزامنة التلقائية
  the look: four ready looks, font, size, style, colour, opacity, border,
  background, shadow, blur, line spacing, position (Kodi's own subtitle
  settings, applied to the line on screen as they change)
  إعدادات Kodi للترجمة
Audio: the track (side page), delay, volume, amplification, passthrough,
stereo upmix, Kodi's audio dialog.

A Dex Hub window (WindowXMLDialog, dexhub_subtitles.xml): Left and Right
change the focused row in this window's own Python. Delay, position and
amplification are player actions: 5.10.315 sent them to the full-screen
window, which does not handle them (Kodi's player controller listens on
the application), so they did nothing; they now go to the application.

Kodi's own subtitle search dialog is no longer opened from here: the search
runs inside the page and its results open beside it, in the same design.
"""
import os
import re
import threading
import time

import xbmc
import xbmcgui

from . import common as C
from . import substyle as S

LEFT, RIGHT, UP, DOWN = 1, 2, 3, 4
CLOSE = (92, 10, 13, 216, 247, 257, 275, 61448, 61467)
MAIN, SIDE, HEAD, DEX, KODI = 100, 200, 210, 220, 300
PROVIDER = 'dhs.subpage.provider'

_AR = {'ar': 'العربية', 'en': 'الإنجليزية', 'fr': 'الفرنسية', 'es': 'الإسبانية', 'de': 'الألمانية',
       'it': 'الإيطالية', 'tr': 'التركية', 'fa': 'الفارسية', 'ur': 'الأردية', 'hi': 'الهندية', 'ru': 'الروسية',
       'pt': 'البرتغالية', 'nl': 'الهولندية', 'ja': 'اليابانية', 'ko': 'الكورية', 'zh': 'الصينية',
       'id': 'الإندونيسية', 'ms': 'الملايوية', 'he': 'العبرية', 'el': 'اليونانية', 'pl': 'البولندية',
       'sv': 'السويدية', 'no': 'النرويجية', 'da': 'الدنماركية', 'fi': 'الفنلندية', 'ro': 'الرومانية',
       'hu': 'المجرية', 'cs': 'التشيكية', 'th': 'التايلاندية', 'vi': 'الفيتنامية', 'bn': 'البنغالية',
       'uk': 'الأوكرانية', 'bg': 'البلغارية', 'hr': 'الكرواتية', 'sr': 'الصربية'}


def _t(ar, en):
    return S._t(ar, en)


def _english():
    return bool(_t('', 'en'))


def _rpc(method, params=None):
    return S._rpc(method, params)


def _player_id():
    for player in (_rpc('Player.GetActivePlayers').get('result') or []):
        if player.get('type') == 'video':
            return player.get('playerid', 1)
    return 1


def _props(*names):
    return (_rpc('Player.GetProperties', {'playerid': _player_id(), 'properties': list(names)})
            .get('result') or {})


def _code(value):
    """ISO 639-1 of a track's language ('ara', 'Arabic', 'ar-SA' -> 'ar')."""
    text = str(value or '').strip()
    if not text:
        return 'und'
    try:
        from ..playback.subtitle_files import _normalize_lang
        key = _normalize_lang(text)
        # it hands back the text itself when it knows no language in it
        if key and key != 'und' and re.match(r'^[a-z]{2,3}(-[a-z]{2})?$', key):
            return key
    except Exception:
        pass
    try:
        short = xbmc.convertLanguage(text.split('-')[0], xbmc.ISO_639_1)
        if short:
            return short.lower()
    except Exception:
        pass
    return 'und'


def _lang_label(code):
    if not code or code == 'und':
        return _t('لغة غير معروفة', 'Unknown language')
    if not _english() and code in _AR:
        return _AR[code]
    try:
        return xbmc.convertLanguage(code, xbmc.ENGLISH_NAME) or code.upper()
    except Exception:
        return code.upper()


def _preferred():
    try:
        from ..playback.subtitle_files import _preferred_subtitle_lang_keys
        keys = [k for k in _preferred_subtitle_lang_keys() if k]
        if keys:
            return keys
    except Exception:
        pass
    return ['ar', 'en']


def _clean_name(name):
    text = str(name or '').strip()
    for tail in ('(External)', '(خارجية)'):
        if text.endswith(tail):
            text = text[:-len(tail)].strip()
    for ext in ('.srt', '.ass', '.ssa', '.vtt', '.sub'):
        if text.lower().endswith(ext):
            text = text[:-len(ext)]
    # flags (regional indicator pairs) and other emoji
    text = ''.join(ch for ch in text if not (0x1F1E6 <= ord(ch) <= 0x1F1FF or 0x1F300 <= ord(ch) <= 0x1FAFF
                                              or 0x2600 <= ord(ch) <= 0x27BF or ord(ch) in (0xFE0F, 0x200D)))
    try:
        from ..subtitle_service import _clean
        text = _clean(text, text)
    except Exception:
        pass
    return text.strip(' -·') or ''


_GENERIC = re.compile(r'(?i)^(?:srt|ass|ssa|vtt|sub|subs|subtitle|subtitles|external|embedded|forced|sdh|cc|hi|ai|'
                      r'utf8|utf|[a-z]{2,3}|v\d+|\d+)$')


def _release_name(name, provider, lang):
    """The subtitle's own name when it has one ('' when it is only the
    add-on, the language and the format: most Stremio add-ons give none)."""
    from . import streamrows
    text = str(name or '').strip()
    if not text:
        return ''
    words = re.findall(r'[^\W_]+', text.lower(), re.UNICODE)
    drop = set(re.findall(r'[^\W_]+', str(provider or '').lower(), re.UNICODE))
    drop.update(streamrows._NAMES)
    drop.update((_lang_label(lang).lower(), str(lang or '').lower()))
    left = [w for w in words if w not in drop and not _GENERIC.match(w)]
    return text if len(''.join(left)) >= 3 else ''


def _readable(name):
    """v5.10.323: a release's dotted file name with spaces, so the page can
    wrap it and show it whole (Widows.Bay.S01E02.1080p -> Widows Bay S01E02 1080p)."""
    text = re.sub(r'\.(srt|ass|ssa|vtt|sub|zip)$', '', str(name or '').strip(), flags=re.I)
    if text.count(' ') <= 1 and (text.count('.') + text.count('_')) >= 2:
        text = re.sub(r'[._](?=\d{3,4}p\b)', ' ', text, flags=re.I)
        text = re.sub(r'(?<!\d)[._](?!\d)|(?<=\d)[._](?=\D)|(?<=\D)[._](?=\d)|_', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
    return text


def _act(name):
    """A player action, to the application (Kodi's player controller listens
    there), waited for; its slider is closed again, the page shows the value."""
    xbmc.executebuiltin('Action(%s)' % name, True)
    xbmc.executebuiltin('Dialog.Close(sliderdialog,true)')


class SubtitleWindow(xbmcgui.WindowXMLDialog):

    def __init__(self, *args, **kwargs):
        super(SubtitleWindow, self).__init__(*args)
        self.section = kwargs.get('section') or ''
        self.kind = kwargs.get('kind') or 'subtitles'
        self.rows = []
        self.side_rows = []
        self.side = ''              # '', 'tracks', 'online'
        self.list = None
        self.side_list = None
        self.found = None           # the add-ons' subtitles, while the page is open
        self.searching = False
        self.lock = threading.RLock()
        self.closed = False
        self.threads = []
        self.providers = None
        self.prov = 0
        self.query = ''
        self.head_rows = []

    # ------------------------------------------------------------ main rows
    def _audio_rows(self):
        got = _props('currentaudiostream', 'audiostreams')
        current = got.get('currentaudiostream') or {}
        from . import streamrows
        name = streamrows.short_audio(current) if current else ''
        rows = [{'id': 'astream', 'label': _t('مسار الصوت', 'Audio track'), 'value': name or '-',
                 'adjust': len(got.get('audiostreams') or []) > 1, 'opens': True, 'kick': _t('الصوت', 'AUDIO')}]
        rows.append({'id': 'adelay', 'label': _t('تأخير الصوت', 'Audio delay'),
                     'value': xbmc.getInfoLabel('Player.AudioDelay') or '0.000 s', 'adjust': True})
        volume = ((_rpc('Application.GetProperties', {'properties': ['volume']}).get('result') or {}).get('volume'))
        rows.append({'id': 'volume', 'label': _t('مستوى الصوت', 'Volume'),
                     'value': '%s %%' % volume if volume is not None else '', 'adjust': True,
                     'pct': max(1, min(99, int(volume or 0))) if volume is not None else 0})
        rows.append({'id': 'amp', 'label': _t('تضخيم الصوت', 'Volume amplification'), 'value': '', 'adjust': True})
        for key, ar, en in (('audiooutput.passthrough', 'تمرير الصوت للجهاز', 'Passthrough'),
                            ('audiooutput.stereoupmix', 'توسيع الستيريو', 'Stereo upmix')):
            value = S._get(key)
            if value is None:
                continue
            rows.append({'id': key, 'label': _t(ar, en), 'value': _t('مفعّل', 'On') if value else _t('مطفأ', 'Off'),
                         'adjust': True, 'toggle': True})
        rows.append({'id': 'kodi_audio', 'label': _t('إعدادات Kodi', 'Kodi settings'), 'value': '',
                     'opens': True, 'kick': _t('المزيد', 'MORE')})
        return rows

    def _rows(self):
        if self.kind == 'audio':
            return self._audio_rows()
        rows = []
        state = _props('currentsubtitle', 'subtitleenabled', 'subtitles')
        enabled = bool(state.get('subtitleenabled'))
        current = state.get('currentsubtitle') or {}
        tracks = state.get('subtitles') or []
        if enabled and current:
            from . import streamrows
            value = _lang_label(streamrows.track_code(current))
        else:
            value = _t('مطفأة', 'Off')
        count = (_t('%d نتيجة', '%d found') % len(self.found)) if self.found else (
            _t('أبحث…', 'Searching…') if self.searching else '')
        rows.append({'id': 'track', 'label': _t('الترجمة', 'Subtitle'), 'value': value, 'adjust': bool(tracks),
                     'opens': True, 'kick': _t('الترجمة', 'SUBTITLES')})
        rows.append({'id': 'online', 'label': _t('بحث عن ترجمة', 'Find subtitles'),
                     'value': count or self._provider().get('name', ''), 'opens': True})
        rows.append({'id': 'show', 'label': _t('إظهار الترجمة', 'Show subtitles'),
                     'value': _t('مفعّلة', 'On') if enabled else _t('مطفأة', 'Off'), 'adjust': True})
        rows.append({'id': 'delay', 'label': _t('التأخير', 'Delay'),
                     'value': xbmc.getInfoLabel('Player.SubtitleDelay') or '0.000 s', 'adjust': True})
        rows.append({'id': 'shift', 'label': _t('رفع وخفض الترجمة', 'Move up or down'),
                     'value': '', 'adjust': True})
        rows.append({'id': 'sync', 'label': _t('مزامنة تلقائية', 'Auto sync'), 'value': '', 'opens': True})
        preset = xbmcgui.Window(10000).getProperty('dhs.substyle.preset')
        rows.append({'id': 'preset', 'label': _t('شكل جاهز', 'Look'),
                     'value': dict(PRESET_NAMES).get(preset, ('', ''))[1 if _english() else 0] or _t('اختر', 'Choose'),
                     'adjust': True, 'kick': _t('الشكل', 'LOOK')})
        settings = S._all()
        background = (settings.get('subtitles.backgroundtype') or {}).get('value')
        for row in S.ROWS:
            key, ar, en, kind, extra = row
            setting = settings.get(key)
            if not setting:
                continue
            if key in S._SHOWN_IF and background not in S._SHOWN_IF[key]:
                continue
            value = setting.get('value')
            entry = {'id': key, 'label': _t(ar, en), 'value': S._value_text(row, setting, value), 'adjust': True}
            if kind == 'colour':
                entry['swatch'] = str(value or 'FFFFFFFF')
            if kind in ('pct', 'int'):
                low, high, _step = extra
                try:
                    entry['pct'] = max(1, min(99, int(round((float(value) - low) * 100.0 / max(1, high - low)))))
                except Exception:
                    entry['pct'] = 1
            rows.append(entry)
        if S.fonts_waiting():
            rows.append({'id': 'note', 'label': _t('خطوط عربية جديدة', 'New Arabic fonts'),
                         'value': _t('بعد إعادة التشغيل', 'after a restart'), 'kick': _t('المزيد', 'MORE')})
        rows.append({'id': 'kodi', 'label': _t('إعدادات Kodi', 'Kodi settings'), 'value': '',
                     'opens': True, 'kick': '' if S.fonts_waiting() else _t('المزيد', 'MORE')})
        return rows

    @staticmethod
    def _item(row):
        li = xbmcgui.ListItem(label=row.get('label') or '', label2=row.get('value') or '', offscreen=True)
        li.setProperties({'id': str(row.get('id') or ''), 'adjust': '1' if row.get('adjust') else '',
                          'opens': '1' if row.get('opens') else '', 'kick': row.get('kick') or '',
                          'swatch': row.get('swatch') or '', 'pct': str(row.get('pct') or ''),
                          'current': '1' if row.get('current') else '', 'busy': '1' if row.get('busy') else '',
                          'flag': row.get('flag') or ''})
        if row.get('pct'):
            try:
                li.getVideoInfoTag().setResumePoint(float(row['pct']), 100.0)
            except Exception:
                pass
        return li

    def build(self, keep=''):
        with self.lock:
            self.rows = self._rows()
            position = 0
            if keep:
                position = next((i for i, r in enumerate(self.rows) if r['id'] == keep), 0)
            elif self.section == 'look':
                position = next((i for i, r in enumerate(self.rows) if r['id'] == 'preset'), 0)
            self.list.reset()
            self.list.addItems([self._item(r) for r in self.rows])
            self.list.selectItem(position)
        if not self.side:
            self.setFocusId(MAIN)

    def refresh_row(self, index):
        """The rows' values again (the whole list when a change shows or
        hides rows: the background's). index -1: every row."""
        with self.lock:
            rows = self._rows()
            if [r['id'] for r in rows] != [r['id'] for r in self.rows]:
                keep = self.rows[index]['id'] if 0 <= index < len(self.rows) else ''
                self.build(keep=keep)
                return
            self.rows = rows
            for i in ([index] if index >= 0 else range(len(rows))):
                row = rows[i]
                li = self.list.getListItem(i)
                li.setLabel2(row.get('value') or '')
                li.setProperty('swatch', row.get('swatch') or '')
                li.setProperty('pct', str(row.get('pct') or ''))
                if row.get('pct'):
                    try:
                        li.getVideoInfoTag().setResumePoint(float(row['pct']), 100.0)
                    except Exception:
                        pass

    def refresh_id(self, row_id):
        index = next((i for i, r in enumerate(self.rows) if r['id'] == row_id), -1)
        if index >= 0:
            self.refresh_row(index)

    # ------------------------------------------------------------ side page
    def _track_rows(self):
        if self.kind == 'audio':
            got = _props('currentaudiostream', 'audiostreams')
            current = (got.get('currentaudiostream') or {}).get('index')
            out = []
            for stream in got.get('audiostreams') or []:
                code = _code(stream.get('language'))
                bits = [x for x in (str(stream.get('codec') or '').upper(),
                                    ('%s ch' % stream.get('channels')) if stream.get('channels') else '') if x]
                from . import streamrows
                look = streamrows.props('audio', stream)
                out.append({'id': 'a:%s' % stream.get('index'), 'label': look.get('dh_title') or _lang_label(code),
                            'value': '  ·  '.join(x for x in (_lang_label(code), look.get('dh_tags') or ' · '.join(bits)) if x),
                            'lang': code,
                            'flag': look.get('dh_flag') or '',
                            'current': stream.get('index') == current, 'index': stream.get('index')})
            return self._group(out)
        got = _props('currentsubtitle', 'subtitleenabled', 'subtitles')
        enabled = bool(got.get('subtitleenabled'))
        current = (got.get('currentsubtitle') or {}).get('index') if enabled else None
        out = [{'id': 'off', 'label': _t('بدون ترجمة', 'No subtitles'), 'value': '', 'current': not enabled,
                'flag': 'dexhub/osd/legacy/x.png'}]
        rows = []
        for stream in got.get('subtitles') or []:
            raw = str(stream.get('name') or '')
            from . import streamrows
            code = streamrows.track_code(stream)
            external = 'External' in raw or 'خارجية' in raw or raw.lower().endswith(('.srt', '.ass', '.vtt'))
            tags = []
            if stream.get('isforced'):
                tags.append(_t('إجبارية', 'Forced'))
            if stream.get('isimpaired'):
                tags.append('SDH')
            tags.append(_t('خارجية', 'External') if external else _t('مضمّنة', 'Embedded'))
            known = streamrows._known(stream)
            label = ((known.get('n') or known.get('s')) if known else '') or _clean_name(raw) or _lang_label(code)
            if known and known.get('s') and known.get('s') != label:
                tags.insert(0, known['s'])
            rows.append({'id': 's:%s' % stream.get('index'), 'label': _readable(label),
                         'value': '  ·  '.join([_lang_label(code)] + tags), 'lang': code,
                         'current': stream.get('index') == current,
                         'flag': streamrows.props('subtitle', stream).get('dh_flag') or '',
                         'index': stream.get('index')})
        return out + self._group(rows)

    @staticmethod
    def _group(rows):
        """Your languages first (in your order), then the others; the
        language as the kicker of its first row."""
        rank = dict((c, i) for i, c in enumerate(_preferred()))
        rows.sort(key=lambda r: (rank.get(r.get('lang'), 99), r.get('lang') or ''))
        last = None
        for row in rows:
            lang = row.get('lang')
            row['kick'] = _lang_label(lang) if lang != last else ''
            last = lang
        return rows

    def _online_rows(self):
        if self.searching and not self.found:
            return [{'id': 'wait', 'label': _t('أسأل إضافات الترجمة…', 'Asking the subtitle add-ons…'),
                     'value': '', 'busy': True}]
        if not self.found:
            return [{'id': 'again', 'label': _t('ما لقيت ترجمة', 'No subtitles found'),
                     'value': _t('اضغط للبحث مرة ثانية', 'Press to search again')}]
        from .. import subtitle_service as SS
        from . import streamrows
        rows = []
        seen = {}
        for i, row in enumerate(self.found):
            lang = SS._base(row.get('lang')) or 'und'
            provider = SS._clean(row.get('sourceName') or 'Dex Hub', 'Dex Hub')
            release = _release_name(SS._row_name(row), provider, lang)
            key = (provider, lang)
            seen[key] = seen.get(key, 0) + 1
            details = [_lang_label(lang), provider if release else '']
            if SS._is_ai(row):
                details.append(_t('ذكاء اصطناعي', 'AI'))
            hearing = row.get('hearing_impaired') or row.get('hi') or row.get('sdh')
            if hearing in (True, 'true', '1', 1):
                details.append('SDH')
            rows.append({'id': 'r:%d' % i, 'label': _readable(release) or provider, '_n': seen[key], '_key': key,
                         'value': '  ·  '.join(x for x in details if x),
                         'flag': streamrows.OWN_FLAGS.get(lang) or (streamrows.FLAG_DIR + lang + '.png'
                                                                    if lang in streamrows.FLAGS else ''),
                         'current': bool(row.get('_loaded')), 'busy': bool(row.get('_loading'))})
        # the same add-on's subtitles told apart: "OpenSubtitles v3  ·  2"
        for row in rows:
            if seen[row['_key']] > 1 and row['label'] == row['_key'][0]:
                row['label'] = '%s  ·  %d' % (row['label'], row['_n'])
        return rows

    # ------------------------------------------------------------ providers
    def _providers(self):
        """Dex Hub (the subtitle add-ons it asks itself) and every subtitle
        service installed in Kodi, as Kodi's subtitle dialog lists them."""
        if self.providers is None:
            out = [{'id': 'dexhub', 'name': 'Dex Hub', 'icon': 'dexhub/osd/legacy/download.png'}]
            got = _rpc('Addons.GetAddons', {'type': 'xbmc.subtitle.module', 'enabled': True,
                                            'properties': ['name', 'thumbnail']})
            for addon in ((got.get('result') or {}).get('addons') or []):
                aid = str(addon.get('addonid') or '')
                if not aid or aid.startswith('plugin.video.dexhub'):
                    continue
                out.append({'id': aid, 'name': addon.get('name') or aid, 'icon': addon.get('thumbnail') or ''})
            self.providers = out
            last = xbmcgui.Window(10000).getProperty(PROVIDER)
            self.prov = next((i for i, p in enumerate(out) if p['id'] == last), 0)
        return self.providers

    def _provider(self):
        providers = self._providers()
        return providers[self.prov % len(providers)] if providers else {}

    def _service_url(self):
        from urllib.parse import quote
        names = []
        for code in _preferred():
            try:
                name = xbmc.convertLanguage(code.split('-')[0], xbmc.ENGLISH_NAME)
            except Exception:
                name = ''
            if name and name not in names:
                names.append(name)
        names = names or ['English']
        base = 'plugin://%s/?action=%s&languages=%s&preferredlanguage=%s' % (
            self._provider()['id'], 'manualsearch' if self.query else 'search', quote(','.join(names)), quote(names[0]))
        if self.query:
            base += '&searchstring=' + quote(self.query)
        return base

    def _head_rows(self):
        provider = self._provider()
        count = len(self._providers())
        return [{'id': 'prov', 'kick': _t('المزوّد', 'PROVIDER'), 'label': provider.get('name', ''),
                 'value': '%d / %d' % (self.prov % count + 1, count), 'adjust': count > 1,
                 'flag': provider.get('icon') or 'dexhub/osd/legacy/download.png'},
                {'id': 'manual', 'label': _t('بحث بالاسم', 'Search by name'),
                 'value': self.query or '', 'opens': True, 'flag': 'dexhub/osd/legacy/search.png'}]

    def fill_head(self):
        self.head_rows = self._head_rows()
        at = self.head_list.getSelectedPosition() if self.head_list.size() else 0
        self.head_list.reset()
        self.head_list.addItems([self._item(r) for r in self.head_rows])
        self.head_list.selectItem(max(0, at))

    def use_provider(self, step=0, fresh=False):
        providers = self._providers()
        self.prov = (self.prov + step) % len(providers)
        provider = self._provider()
        xbmcgui.Window(10000).setProperty(PROVIDER, provider['id'])
        self.fill_head()
        self.setProperty('side_wait', _t('أبحث في %s…', 'Searching %s…') % provider['name'])
        self.setProperty('side_none', _t('ما لقى %s ترجمة', '%s found nothing') % provider['name'])
        if provider['id'] == 'dexhub':
            self.setProperty('src', 'dexhub')
            self.side_list = self.dex_list
            if fresh or (self.found is None and not self.searching):
                self.search()
            self.side_rows = []
            self.fill_side(focus_current=True)
        else:
            self.setProperty('src', 'kodi')
            self.setProperty('kodi_url', self._service_url())

    def ask_name(self):
        default = self.query or xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title')
        text = xbmcgui.Dialog().input(_t('اسم العمل', 'Title'), default)
        if text is None:
            return
        self.query = text.strip()
        self.use_provider(fresh=self._provider().get('id') == 'dexhub')

    def open_side(self, mode):
        self.side = mode
        if mode == 'tracks':
            title = _t('مسارات الصوت', 'Audio tracks') if self.kind == 'audio' else _t('الترجمات', 'Subtitles')
            icon = 'dexhub/osd/legacy/audio-lines.png' if self.kind == 'audio' else 'dexhub/osd/legacy/languages.png'
            hint = _t('اختر، ويمين للرجوع', 'Choose; Right goes back')
            self.side_list = self.track_list
            self.side_rows = []
            self.fill_side(focus_current=True)
            focus = SIDE
        else:
            title = _t('بحث عن ترجمة', 'Find subtitles')
            icon = 'dexhub/osd/legacy/search.png'
            hint = _t('يمين ويسار يغيّرون المزوّد، وتحت للنتائج', 'Left and Right change the provider; Down for results')
            self.use_provider()
            focus = HEAD
        self.setProperty('side_title', title)
        self.setProperty('side_icon', icon)
        self.setProperty('side_hint', hint)
        self.setProperty('side', mode)
        self.setFocusId(focus)

    def close_side(self):
        was = self.side
        self.side = ''
        self.clearProperty('side')
        self.setFocusId(MAIN)
        self.refresh_id('astream' if self.kind == 'audio' else 'track')
        if was == 'online':
            self.refresh_id('online')

    def _side_ids(self):
        return (SIDE,) if self.side == 'tracks' else (HEAD, KODI if self.getProperty('src') == 'kodi' else DEX)

    def fill_side(self, focus_current=False):
        with self.lock:
            if self.side == 'online' and self.side_list is not self.dex_list:
                return
            rows = self._track_rows() if self.side == 'tracks' else self._online_rows()
            same = bool(self.side_rows) and [r['id'] for r in rows] == [r['id'] for r in self.side_rows]
            at = self.side_list.getSelectedPosition() if self.side_rows else 0
            self.side_rows = rows
            if same:
                for i, row in enumerate(rows):
                    li = self.side_list.getListItem(i)
                    li.setLabel2(row.get('value') or '')
                    li.setProperty('current', '1' if row.get('current') else '')
                    li.setProperty('busy', '1' if row.get('busy') else '')
                return
            self.side_list.reset()
            self.side_list.addItems([self._item(r) for r in rows])
            if focus_current:
                at = next((i for i, r in enumerate(rows) if r.get('current')), 0)
            self.side_list.selectItem(max(0, min(at, len(rows) - 1)))

    def pick(self):
        index = self.side_list.getSelectedPosition()
        if not 0 <= index < len(self.side_rows):
            return
        row = self.side_rows[index]
        rid = row['id']
        if rid == 'off':
            xbmc.Player().showSubtitles(False)
        elif rid.startswith('s:'):
            _rpc('Player.SetSubtitle', {'playerid': _player_id(), 'subtitle': row['index'], 'enable': True})
        elif rid.startswith('a:'):
            _rpc('Player.SetAudioStream', {'playerid': _player_id(), 'stream': row['index']})
        elif rid == 'again':
            self.search()
            self.fill_side()
            return
        elif rid.startswith('r:'):
            self.download(int(rid[2:]))
            return
        else:
            return
        xbmc.sleep(150)
        self.fill_side()
        xbmc.sleep(150)
        self.close_side()

    # ------------------------------------------------------------ online
    def search(self):
        if self.searching:
            return
        self.searching = True
        self.found = None
        self._start(self._search)

    def _start(self, target, *args):
        th = threading.Thread(target=target, args=args, name='DexHubSubPage', daemon=True)
        self.threads.append(th)
        th.start()

    def _search(self):
        found = []
        try:
            from .. import subtitle_service as SS
            from .. import subtitle_broker
            rows = subtitle_broker.search_subtitles(SS._playing_ctx(manual_title=self.query),
                                                    max_results=SS._MAX_RESULTS)
            found = SS._order(rows, _preferred())
        except Exception as exc:
            C.log('subtitle page: search failed: %s' % exc, xbmc.LOGWARNING)
        self.found = found
        self.searching = False
        C.log('subtitle page: %d subtitle(s) from the add-ons' % len(found))
        if self.closed:
            return
        try:
            if self.side == 'online':
                self.fill_side()
            self.refresh_id('online')
        except Exception:
            pass

    def download(self, i):
        if not self.found or not 0 <= i < len(self.found):
            return
        row = self.found[i]
        if row.get('_loading'):
            return
        row['_loading'] = True
        self.fill_side()
        self._start(self._download, row)

    def _download(self, row):
        local = ''
        try:
            from .. import subtitle_service as SS
            from ..playback import subtitle_files as sf
            url = str(row.get('url') or '')
            lang = str(row.get('lang') or 'und')
            name = SS._row_name(row)
            ext = sf._detect_subtitle_extension({'format': row.get('format') or row.get('codec') or ''}, url)
            stem = (sf._safe_subtitle_filename_part(name, limit=60) or 'subtitle').replace(' ', '.')
            target = os.path.join(sf._subs_tmp_dir(), 'dexhub.%s.%s.%s' % (stem, SS._base(lang) or 'und', ext))
            local = sf._download_remote_subtitle(url, target) or ''
            if local:
                from ..playback import subtitle_names
                subtitle_names.remember(local, name, SS._clean(row.get('sourceName') or '', ''), SS._base(lang))
        except Exception as exc:
            C.log('subtitle page: download failed: %s' % exc, xbmc.LOGWARNING)
        row['_loading'] = False
        if local:
            for other in self.found or []:
                other['_loaded'] = False
            row['_loaded'] = True
            try:
                player = xbmc.Player()
                player.setSubtitles(local)
                player.showSubtitles(True)
            except Exception as exc:
                C.log('subtitle page: could not show %s: %s' % (local, exc), xbmc.LOGWARNING)
            C.log('subtitle page: showing %s' % os.path.basename(local))
        else:
            xbmcgui.Dialog().notification('Dex Hub', _t('ما قدرت أنزل هذي الترجمة', 'Could not download it'),
                                          xbmcgui.NOTIFICATION_WARNING, 2500, False)
        if self.closed:
            return
        try:
            if self.side == 'online':
                self.fill_side()
            time.sleep(0.4)
            if not self.closed:
                self.refresh_id('track')
        except Exception:
            pass

    def service_pick(self):
        path = xbmc.getInfoLabel('Container(%d).ListItem.FileNameAndPath' % KODI)
        if not path:
            return
        info = {'name': xbmc.getInfoLabel('Container(%d).ListItem.Label2' % KODI),
                'lang': xbmc.getInfoLabel('Container(%d).ListItem.Thumb' % KODI),
                'source': self._provider().get('name', '')}
        self.setProperty('side_hint', _t('أنزّل الترجمة…', 'Downloading…'))
        self._start(self._service_download, path, info)

    def _service_download(self, path, info):
        """What Kodi's subtitle dialog does with a result: list its download
        path (the service writes the file and answers with it), then show
        it. The file is copied next to Dex Hub's own subtitle files first
        (a service may clear its folder)."""
        local = ''
        try:
            import xbmcvfs
            from ..playback import subtitle_files as sf
            from ..playback import subtitle_names
            got = _rpc('Files.GetDirectory', {'directory': path, 'media': 'files'})
            files = [f.get('file') for f in ((got.get('result') or {}).get('files') or []) if f.get('file')]
            for src in files:
                ext = src.rsplit('.', 1)[-1].lower() if '.' in src[-6:] else 'srt'
                if ext not in ('srt', 'ass', 'ssa', 'vtt', 'sub', 'smi', 'txt', 'idx', 'sup'):
                    continue
                stem = (sf._safe_subtitle_filename_part(info['name'] or 'subtitle', limit=60) or 'subtitle')
                lang = (info['lang'] or 'und').split('-')[0][:3]
                target = os.path.join(sf._subs_tmp_dir(), '%s - %s - %s.%s' % (
                    stem, lang, sf._safe_subtitle_filename_part(info['source'], 30) or 'service', ext))
                local = target if xbmcvfs.copy(src, target) else src
                subtitle_names.remember(local, info['name'], info['source'], lang)
                break
        except Exception as exc:
            C.log('subtitle page: %s download failed: %s' % (info.get('source'), exc), xbmc.LOGWARNING)
        if local:
            try:
                player = xbmc.Player()
                player.setSubtitles(local)
                player.showSubtitles(True)
            except Exception as exc:
                C.log('subtitle page: could not show %s: %s' % (local, exc), xbmc.LOGWARNING)
            C.log('subtitle page: showing %s from %s' % (os.path.basename(local), info.get('source')))
        if self.closed:
            return
        self.setProperty('side_hint', (_t('تعمل الآن: %s', 'Showing: %s') % (info['name'] or info['source']))
                         if local else _t('ما قدرت أنزل هذي الترجمة', 'Could not download it'))
        try:
            time.sleep(0.4)
            if not self.closed:
                self.refresh_id('track')
        except Exception:
            pass

    # ------------------------------------------------------------ Kodi
    def onInit(self):
        self.list = self.getControl(MAIN)
        self.track_list = self.getControl(SIDE)
        self.head_list = self.getControl(HEAD)
        self.dex_list = self.getControl(DEX)
        self.side_list = self.track_list
        self.setProperty('title', _t('إعدادات الصوت', 'Audio settings') if self.kind == 'audio'
                         else _t('إعدادات الترجمة', 'Subtitle settings'))
        self.setProperty('icon', 'dexhub/osd/legacy/audio-lines.png' if self.kind == 'audio'
                         else 'dexhub/osd/legacy/captions.png')
        self.setProperty('hint', _t('يمين ويسار للتغيير، وOK يفتح القائمة', 'Left and Right change it; OK opens the list'))
        self.build()
        if self.section == 'tracks':
            self.open_side('tracks')

    def onAction(self, action):
        aid = action.getId()
        focus = self.getFocusId()
        # v5.10.331: the subtitle services' list is filled by Kodi (its
        # <content>), so Kodi handled OK on it itself: it opened the item
        # (the Home came up over the video) and onClick never ran. The list
        # now has a no-op onclick and its pick is taken here.
        if focus == KODI and aid in (7, 100):
            self.service_pick()
            return
        if aid in CLOSE:
            if self.side:
                self.close_side()
            else:
                self.close()
            return
        if self.side:
            ids = self._side_ids()
            if focus == HEAD and aid in (LEFT, RIGHT) and self.head_list.getSelectedPosition() == 0:
                self.use_provider(-1 if aid == LEFT else 1)
            elif aid == RIGHT:
                self.close_side()
            elif focus not in ids and aid in (LEFT, UP, DOWN):
                self.setFocusId(ids[0])
            return
        if focus != MAIN:
            if aid in (LEFT, RIGHT, UP, DOWN):
                self.setFocusId(MAIN)
            return
        if aid in (LEFT, RIGHT):
            self.change(-1 if aid == LEFT else 1)

    def onClick(self, control_id):
        if control_id in (SIDE, DEX):
            self.pick()
            return
        if control_id == HEAD:
            if self.head_list.getSelectedPosition() == 0:
                self.use_provider(1)
            else:
                self.ask_name()
            return
        if control_id == KODI:
            self.service_pick()
            return
        if control_id != MAIN:
            return
        index = self.list.getSelectedPosition()
        row = self.rows[index] if 0 <= index < len(self.rows) else {}
        rid = row.get('id')
        if rid in ('track', 'astream'):
            self.open_side('tracks')
        elif rid == 'online':
            self.open_side('online')
        elif rid == 'sync':
            self.close()
            xbmc.executebuiltin('RunPlugin(%s)' % C.url('subtitle_sync'))
        elif rid == 'kodi':
            self.close()
            xbmc.executebuiltin('ActivateWindow(osdsubtitlesettings)')
        elif rid == 'kodi_audio':
            self.close()
            xbmc.executebuiltin('ActivateWindow(osdaudiosettings)')
        elif row.get('adjust'):
            self.change(1)

    def change(self, step):
        index = self.list.getSelectedPosition()
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        if not row.get('adjust'):
            return
        key = row['id']
        if key == 'show':
            xbmc.Player().showSubtitles(not bool(_props('subtitleenabled').get('subtitleenabled')))
        elif key == 'track':
            _rpc('Player.SetSubtitle', {'playerid': _player_id(), 'subtitle': 'next' if step > 0 else 'previous',
                                        'enable': True})
        elif key == 'delay':
            _act('SubtitleDelayPlus' if step > 0 else 'SubtitleDelayMinus')
        elif key == 'shift':
            _act('SubtitleShiftUp' if step > 0 else 'SubtitleShiftDown')
        elif key == 'astream':
            _rpc('Player.SetAudioStream', {'playerid': _player_id(), 'stream': 'next' if step > 0 else 'previous'})
        elif key == 'adelay':
            _act('AudioDelayPlus' if step > 0 else 'AudioDelayMinus')
        elif key == 'volume':
            _rpc('Application.SetVolume', {'volume': 'increment' if step > 0 else 'decrement'})
        elif key == 'amp':
            _act('VolAmpUp' if step > 0 else 'VolAmpDown')
        elif row.get('toggle'):
            S._set(key, not bool(S._get(key)))
        elif key == 'preset':
            names = [n for n, _l in PRESET_NAMES]
            current = xbmcgui.Window(10000).getProperty('dhs.substyle.preset')
            at = names.index(current) if current in names else -1
            S.preset({'name': names[(at + step) % len(names)]})
            self.build(keep='preset')
            return
        else:
            S.step({'key': key, 'step': str(step)})
        xbmc.sleep(80)          # Kodi's own changes (show, track) land first
        self.refresh_row(index)


PRESET_NAMES = (('dexhub', ('Dex Hub', 'Dex Hub')), ('cinema', ('سينما', 'Cinema')),
                ('box', ('صندوق', 'Box')), ('large', ('كبير', 'Large')))


def open_window(params):
    import xbmcaddon
    try:
        # the colours the page is drawn with exist (v5.10.298)
        from .. import skin_theme
        skin_theme.ensure_theme()
    except Exception:
        pass
    # the quick list behind (the player's Audio or Subtitles panel) closes
    xbmc.executebuiltin('Dialog.Close(1146,true)')
    xbmc.executebuiltin('Dialog.Close(1147,true)')
    addon = xbmcaddon.Addon(C.ADDON_ID)
    window = SubtitleWindow('dexhub_subtitles.xml', addon.getAddonInfo('path'), 'Default', '1080i',
                            section=params.get('section') or '',
                            kind='audio' if params.get('kind') == 'audio' else 'subtitles')
    window.doModal()
    window.closed = True
    # a search or a download still running ends here, before the add-on's
    # interpreter does (a thread calling Kodi after it would be unsafe)
    for th in list(window.threads):
        th.join(20)
    del window
    return None
