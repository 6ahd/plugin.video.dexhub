# -*- coding: utf-8 -*-
"""The sources page's filter as a side panel (v5.10.557; window dexhub_filter.xml, written by
tools/s557/make_filter_window.py; the layout is the user's own picture).

Before, the page had a drawer with one choice at a time (4K, or Remux, or one add-on). Now
several things hold together: 4K + Dolby Vision + Remux. Inside a section the choices add up
(Dolby Vision or HDR10+), across sections they narrow. Every chip shows the count it would
leave, counted against the other sections, so one that leads nowhere reads 0 before it is
pressed.

The panel stands left of the sources: Left on the sources opens it, Right from a control's
last place (or Back) closes it, and the sources follow every press at once.
- Smart picks: combinations worked out from the list itself (the best picture this title
  has, a balanced one, a light one, the best sound), one press each.
- Chips: resolution, picture, release, sound.
- Rows that step through their values on OK: file size, bitrate (size over the running
  time), type, add-on, sort.
- Remember filters saves the choice for every title; a kept filter that leaves a title with
  nothing steps aside for that title and says so.
"""
import json
import os
import re

import xbmc
import xbmcgui
import xbmcvfs

from .bar_sections import _t

GROUPS = ('ok', 'tier', 'res', 'pic', 'rel', 'aud', 'size', 'rate', 'type', 'addon', 'sort')
FILTERS = GROUPS[:-1]
SMART, ROWS, KEEP, SHOW, RESET, AWAY = 3001, 3015, 3080, 3090, 3091, 3099
PANELS = ((3010, ('ok', 'tier'), 4), (3011, 'res', 4), (3012, 'pic', 2), (3013, 'rel', 2), (3014, 'aud', 3))
_TIER = re.compile(r'\bTier 0?(\d)\b')
HEAD, CHIP_STEP, PILL_STEP, ROW_STEP = 34, 66, 84, 60        # the window's row heights
MIX, MIX_LEFT, MIX_TOP, MIX_WIDTH = 3201, 44, 124, 712           # the mix bar's four parts
# rows that step through their values on OK: (Arabic, English, the choice it stands for)
STEPS = {'size': (('أي حجم', 'Any', ()), ('أقل من 10 GB', 'Under 10 GB', ('s1', 's2')),
                  ('10 – 25 GB', '10 – 25 GB', ('s3',)), ('فوق 25 GB', 'Over 25 GB', ('s4', 's5'))),
         'rate': (('أي بت ريت', 'Any', ()), ('أقل من 8 Mbps', 'Under 8 Mbit/s', ('r1',)),
                  ('8 – 20 Mbps', '8 – 20 Mbit/s', ('r2',)), ('20 – 40 Mbps', '20 – 40 Mbit/s', ('r3',)),
                  ('فوق 40 Mbps', 'Over 40 Mbit/s', ('r4',)))}
ROW_NAMES = {'size': ('حجم الملف', 'File size'), 'rate': ('البت ريت', 'Bitrate'), 'type': ('النوع', 'Type'),
             'addon': ('الإضافة', 'Add-on'), 'sort': ('الترتيب', 'Sort')}
# each kind's colour (the dot on its chip, its fill when on); the names are the window's
TONE = {'clean': 'green', 'T1': 'gold', 'T2': 'blue', 'T3': 'green',
        '4K': 'gold', '1080': 'blue', '720': 'green', 'SD': 'grey',
        'DV': 'mauve', 'HDR10+': 'amber', 'HDR': 'amber', 'SDR': 'grey',
        'REMUX': 'coral', 'BLURAY': 'blue', 'WEB': 'green', 'DISC': 'gold',
        'ATMOS': 'lav', 'TRUEHD': 'mauve', 'DTSX': 'blue', 'DTSHD': 'blue', 'DDP': 'green', 'DTS': 'grey',
        'best': 'gold', 'even': 'blue', 'light': 'green', 'sound': 'lav'}
A_BACK = {9, 10, 92, 216, 247, 257, 275, 61448, 61467}
_FILE = 'special://profile/addon_data/plugin.video.dexhub/source_filter.json'

HEADINGS = {'ok': ('TRaSH Guides', 'TRaSH Guides'), 'tier': ('TRaSH Guides', 'TRaSH Guides'), 'sort': ('الترتيب', 'Sort'), 'res': ('الدقة', 'Resolution'), 'pic': ('الصورة', 'Picture'),
            'rel': ('النسخة', 'Release'), 'aud': ('الصوت', 'Sound'), 'size': ('الحجم', 'Size'),
            'rate': ('البت ريت · Mbps', 'Bitrate · Mbit/s'), 'type': ('النوع', 'Type'), 'addon': ('الإضافة', 'Add-on')}
NAMES = {'clean': ('نظيف', 'Clean'), 'T1': ('Tier 1', 'Tier 1'), 'T2': ('Tier 2', 'Tier 2'), 'T3': ('Tier 3', 'Tier 3'),
         '4K': ('4K', '4K'), '1080': ('1080p', '1080p'), '720': ('720p', '720p'), 'SD': ('SD', 'SD'),
         'DV': ('Dolby Vision', 'Dolby Vision'), 'HDR10+': ('HDR10+', 'HDR10+'), 'HDR': ('HDR', 'HDR'),
         'SDR': ('SDR', 'SDR'),
         'REMUX': ('Remux', 'Remux'), 'BLURAY': ('Blu-ray', 'Blu-ray'), 'WEB': ('WEB', 'WEB'),
         'DISC': ('قرص كامل', 'Full disc'),
         'ATMOS': ('Atmos', 'Atmos'), 'TRUEHD': ('TrueHD', 'TrueHD'), 'DTSX': ('DTS:X', 'DTS:X'),
         'DTSHD': ('DTS-HD', 'DTS-HD'), 'DDP': ('Dolby Digital+', 'Dolby Digital+'), 'DTS': ('DTS', 'DTS')}
ORDER = {'ok': ('clean',), 'tier': ('T1', 'T2', 'T3'), 'res': ('4K', '1080', '720', 'SD'), 'pic': ('DV', 'HDR10+', 'HDR', 'SDR'),
         'rel': ('REMUX', 'BLURAY', 'WEB', 'DISC'), 'aud': ('ATMOS', 'TRUEHD', 'DTSX', 'DTSHD', 'DDP', 'DTS')}
# (key, from, to, Arabic, English): GB for sizes, Mbit/s for bitrates
SIZES = (('s1', 0, 3, 'أقل من 3 GB', 'Under 3 GB'), ('s2', 3, 10, '3 – 10 GB', '3 – 10 GB'),
         ('s3', 10, 25, '10 – 25 GB', '10 – 25 GB'), ('s4', 25, 50, '25 – 50 GB', '25 – 50 GB'),
         ('s5', 50, 1e9, 'فوق 50 GB', 'Over 50 GB'))
RATES = (('r1', 0, 8, 'أقل من 8', 'Under 8'), ('r2', 8, 20, '8 – 20', '8 – 20'),
         ('r3', 20, 40, '20 – 40', '20 – 40'), ('r4', 40, 1e9, 'فوق 40', 'Over 40'))

_DV = re.compile(r'(?<![A-Z0-9])(?:DV|DOVI|DOLBY[ ._-]?VISION)(?![A-Z0-9])')
_HDRP = re.compile(r'HDR10\+|HDR10[ ._-]?PLUS')
_HDR = re.compile(r'(?<![A-Z0-9])(?:HDR(?:10)?|HLG)(?![A-Z0-9+])')
_REMUX = re.compile(r'REMUX')
_BLURAY = re.compile(r'BLU[ ._-]?RAY|BD[ ._-]?RIP|BR[ ._-]?RIP|(?<![A-Z0-9])BD(?:25|50|66|100)?(?![A-Z0-9])')
_WEB = re.compile(r'WEB[ ._-]?DL|WEB[ ._-]?RIP|(?<![A-Z0-9])WEB(?![A-Z0-9])|(?<![A-Z0-9])(?:AMZN|NF|DSNP|ATVP|HMAX|MAX)(?![A-Z0-9])')
_AUD = (('ATMOS', re.compile(r'ATMOS')), ('TRUEHD', re.compile(r'TRUE[ ._-]?HD')),
        ('DTSX', re.compile(r'DTS[ ._:-]?X(?![A-Z0-9])')),
        ('DTSHD', re.compile(r'DTS[ ._-]?HD|DTS[ ._-]?MA(?![A-Z0-9])')),
        ('DDP', re.compile(r'DD\+|DDP(?![A-Z])|E[ ._-]?AC[ ._-]?3')),
        ('DTS', re.compile(r'(?<![A-Z0-9])DTS(?![ ._:-]?(?:X|HD|MA))')))


def _log(msg):
    xbmc.log('[DexHub] source filter: %s' % msg, xbmc.LOGINFO)


def minutes_of(meta):
    """The title's running time in minutes (0 when unknown)."""
    for key in ('runtime', 'duration', 'episode_runtime'):
        try:
            value = float((meta or {}).get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return int(value / 60) if value > 600 else int(value)
    return 0


def minutes_lookup(meta):
    """The running time from TMDb when the page's own details have none (the TMDb Helper
    route opens the page with ids and a name only). One short call, kept by tmdb_direct."""
    meta = meta or {}
    tmdb_id, imdb_id = str(meta.get('tmdb_id') or '').strip(), str(meta.get('imdb_id') or '').strip()
    if not tmdb_id and not imdb_id.startswith('tt'):
        return 0
    kind = str(meta.get('media_type') or meta.get('type') or 'movie').lower()
    kind = 'tv' if kind in ('series', 'tv', 'show', 'tvshow', 'episode', 'anime') or meta.get('season') else 'movie'
    try:
        from . import tmdb_direct
        found = tmdb_direct.meta_for_cached_only(tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=kind) or \
            tmdb_direct.meta_for(tmdb_id=tmdb_id, imdb_id=imdb_id, media_type=kind, timeout=3, max_attempts=1) or {}
        return int(found.get('runtime') or 0)
    except Exception as exc:
        _log('running time not found: %s' % exc)
        return 0


def _bucket(value, table):
    for key, low, high, _ar, _en in table:
        if low <= value < high:
            return key
    return ''


def _trash(row, anime=False):
    """(tier, ok) by TRaSH Guides: 'T1'..'T3' ('' when the release group is in no tier), and
    'clean' unless the release matches something TRaSH rejects (x265 at HD, AV1, upscaled...)."""
    try:
        from . import trash_guides
        number = trash_guides.tier(row, anime=anime)
        low = trash_guides.rejects(row, anime=anime)
    except Exception:
        return '', 'clean'
    return ('T%d' % min(3, number) if number else ''), ('' if low else 'clean')


def facts(row, minutes=0, anime=False):
    """What a row is, per group: {'res': '4K', 'pic': {'DV', 'HDR'}, ...}. Kept on the row."""
    known = row.get('_sf')
    if known is not None and known.get('_m') == minutes and known.get('_a') == anime:
        return known
    from . import source_browser as B
    blob = B._row_quality_blob(row)
    hints = row.get('behaviorHints') if isinstance(row.get('behaviorHints'), dict) else {}
    blob = ' '.join((blob, str(row.get('filename') or hints.get('filename') or '').upper(),
                     str(row.get('stream_title') or '').upper()))
    quality = str(row.get('quality') or '').upper()
    res = ''
    for text in (quality, blob):
        if any(t in text for t in ('2160', '4K', 'UHD')):
            res = '4K'
        elif '1080' in text or 'FHD' in text:
            res = '1080'
        elif '720' in text:
            res = '720'
        elif any(t in text for t in ('480', '576', '360', 'SD')) and text is quality:
            res = 'SD'
        if res:
            break
    pic = set()
    if _DV.search(blob):
        pic.add('DV')
    if _HDRP.search(blob):
        pic.add('HDR10+')
    elif _HDR.search(blob):
        pic.add('HDR')
    if not pic:
        pic.add('SDR')
    if B._row_is_disc(row):
        rel = 'DISC'
    elif _REMUX.search(blob):
        rel = 'REMUX'
    elif _BLURAY.search(blob):
        rel = 'BLURAY'
    elif _WEB.search(blob):
        rel = 'WEB'
    else:
        rel = ''
    aud = set(key for key, pattern in _AUD if pattern.search(blob))
    size = B._sort_size_bytes(row)
    gigabytes = size / 1073741824.0 if size > 0 else 0.0
    rate = ''
    if gigabytes and minutes:
        mbps = size * 8.0 / (minutes * 60.0) / 1000000.0
        if mbps < 200:                          # a season pack's size is not one episode's
            rate = _bucket(mbps, RATES)
    kind = B._row_type_filter(row)[2:]
    tier, ok = _trash(row, anime)
    out = {'_m': minutes, '_a': anime, 'tier': tier, 'ok': ok, 'res': res, 'pic': pic, 'rel': rel, 'aud': aud,
           'size': _bucket(gigabytes, SIZES) if gigabytes else '', 'rate': rate,
           'type': kind, 'addon': B._row_group_name(row).upper(), 'addon_name': B._row_group_name(row)}
    row['_sf'] = out
    return out


def _has(fact, value):
    return value in fact if isinstance(fact, (set, frozenset)) else fact == value


def matches(fact, chosen, skip=''):
    """True when the row passes every group's choice (the chips of one group add up)."""
    for group in FILTERS:
        wanted = chosen.get(group)
        if not wanted or group == skip:
            continue
        have = fact.get(group)
        if isinstance(have, (set, frozenset)):
            if not have & wanted:
                return False
        elif have not in wanted:
            return False
    return True


def active(chosen):
    return any((chosen or {}).get(group) for group in FILTERS)


def apply(rows, chosen, minutes=0, anime=False):
    if not active(chosen):
        return rows
    return [row for row in rows if matches(facts(row, minutes, anime), chosen)]


def _name(group, value, names=None):
    if group in ('size', 'rate'):
        for key, _low, _high, ar, en in (SIZES if group == 'size' else RATES):
            if key == value:
                return _t(ar, en)
    if group == 'type':
        from . import source_browser as B
        return B._nice_type(value)
    if group == 'addon':
        return (names or {}).get(value) or value.title()
    return _t(*NAMES.get(value, (value, value)))


def summary(chosen, names=None):
    """'4K · Dolby Vision · Remux': what is on, in the groups' order."""
    bits = []
    for group in FILTERS:
        wanted = (chosen or {}).get(group) or ()
        if group in ORDER:
            order = ORDER[group]
        elif group in ('size', 'rate'):
            order = [key for key, _l, _h, _a, _e in (SIZES if group == 'size' else RATES)]
        else:
            order = sorted(wanted)
        bits.extend(_name(group, value, names) for value in order if value in wanted)
    return u' · '.join(bits)


class Model(object):
    """The list's facts, the choice, and the counts that follow from both."""

    def __init__(self, rows, minutes, chosen, anime=False):
        self.minutes = minutes
        self.facts = [facts(row, minutes, anime) for row in rows if isinstance(row, dict)]
        self.chosen = {group: set((chosen or {}).get(group) or ()) for group in FILTERS}
        self.names = {}
        for fact in self.facts:
            self.names.setdefault(fact['addon'], fact['addon_name'])
        self.values = {}
        for group in FILTERS:
            seen = []
            for fact in self.facts:
                have = fact[group]
                for value in (have if isinstance(have, (set, frozenset)) else (have,)):
                    if value and value not in seen:
                        seen.append(value)
            if group in ORDER:
                seen = [v for v in ORDER[group] if v in seen]
            elif group in ('size', 'rate'):
                seen = [k for k, _l, _h, _a, _e in (SIZES if group == 'size' else RATES) if k in seen]
            # one value that every source has leaves nothing to choose between
            lacking = any(not fact[group] for fact in self.facts)
            self.values[group] = seen if len(seen) > 1 or (seen and lacking) or self.chosen[group] else []

    def total(self, chosen=None):
        chosen = self.chosen if chosen is None else chosen
        return sum(1 for fact in self.facts if matches(fact, chosen))

    def count(self, group, value):
        return sum(1 for fact in self.facts if _has(fact[group], value) and matches(fact, self.chosen, skip=group))

    def mix(self):
        """How many of the sources left are 4K, 1080p, 720p; the rest under ''."""
        out = {}
        for fact in self.facts:
            if matches(fact, self.chosen):
                key = fact['res'] if fact['res'] in ('4K', '1080', '720') else ''
                out[key] = out.get(key, 0) + 1
        return out

    def toggle(self, group, value):
        self.chosen[group].symmetric_difference_update((value,))

    def clear(self):
        for group in FILTERS:
            self.chosen[group].clear()

    def picks(self):
        """Smart picks: (key, name, line, choice), each one leaving something and not everything."""
        out = []

        def add(key, name, line, choice):
            choice = {g: set(v) for g, v in choice.items() if v}
            n = self.total(dict({g: set() for g in FILTERS}, **choice))
            if choice and 0 < n < len(self.facts) and all(choice != other[3] for other in out):
                out.append((key, name, line, choice, n))

        def narrow(choice, group, values):
            """Add the first of values that still leaves a source."""
            for value in values:
                trial = dict(choice, **{group: {value}})
                if self.total(dict({g: set() for g in FILTERS}, **trial)):
                    return trial
            return choice

        top = next((v for v in ORDER['res'] if any(f['res'] == v for f in self.facts)), '')
        if top:
            best = narrow(narrow({'res': {top}}, 'rel', ('REMUX', 'BLURAY')), 'pic', ('DV', 'HDR10+', 'HDR'))
            line = summary(best).replace('Dolby Vision', 'DV')
            # v5.10.557: without what TRaSH rejects (a Dolby Vision copy with no HDR fallback, an
            # upscale); the best picture also asks for a Tier 01 group while five or more stay
            best = narrow(best, 'ok', ('clean',))
            tiered = dict(best, tier={'T1'})
            if self.total(dict({g: set() for g in FILTERS}, **tiered)) >= 5:
                best, line = tiered, line + u' \u00b7 Tier 1'
            add('best', _t('أفضل صورة', 'Best picture'), line, best)
            rates = [k for k in ('r3', 'r2') if any(f['rate'] == k for f in self.facts)]
            if rates:
                add('even', _t('متوازن', 'Balanced'), _t('جودة عالية وحجم معقول', 'Quality at a fair size'),
                    narrow({'res': {top}, 'rate': set(rates)}, 'ok', ('clean',)))
            else:
                sizes = [k for k in ('s2', 's3') if any(f['size'] == k for f in self.facts)]
                if sizes:
                    add('even', _t('متوازن', 'Balanced'),
                        _t('جودة عالية وحجم معقول', 'Quality at a fair size'), {'res': {top}, 'size': set(sizes)})
        light = {'rate': {'r1'}} if any(f['rate'] == 'r1' for f in self.facts) else {'size': {'s1'}}
        hd = set(v for v in ('4K', '1080') if any(f['res'] == v for f in self.facts))
        add('light', _t('خفيف وسريع', 'Light and quick'),
            _t('للنت الضعيف والجوال', 'For a slow line'), dict(light, res=hd))
        sound = set(v for v in ('ATMOS', 'TRUEHD', 'DTSX') if any(v in f['aud'] for f in self.facts))
        add('sound', _t('أفضل صوت', 'Best sound'), summary({'aud': sound}), {'aud': sound})
        return out


# ------------------------------------------------------------ kept choice

def remembered():
    """The choice saved with "Keep it" ({} when none)."""
    try:
        with xbmcvfs.File(_FILE) as handle:
            data = json.loads(handle.read() or '{}')
    except Exception:
        return {}
    return {group: set(data.get(group) or ()) for group in FILTERS if data.get(group)}


def remember(chosen):
    try:
        folder = xbmcvfs.translatePath(os.path.dirname(_FILE))
        if not os.path.isdir(folder):
            os.makedirs(folder)
        path = xbmcvfs.translatePath(_FILE)
        if not active(chosen):
            if os.path.isfile(path):
                os.remove(path)
            return
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump({group: sorted(values) for group, values in chosen.items() if values}, handle)
    except Exception as exc:
        _log('not saved: %s' % exc)


# ------------------------------------------------------------ the panel

class FilterWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(FilterWindow, self).__init__(*args)
        self.model = Model(kwargs.get('rows') or [], kwargs.get('minutes') or 0, kwargs.get('chosen'),
                           anime=bool(kwargs.get('anime')))
        self.sorts = list(kwargs.get('sorts') or [])           # [(key, name)]
        self.sort = kwargs.get('sort') or ''
        self.keep = bool(kwargs.get('keep'))
        self.on_change = kwargs.get('on_change')    # the page follows every press at once
        self._picks = []
        self._steps = []                            # [(section, [(name, values)])]
        self._inited = False

    def _set(self, cid, items):
        try:
            control = self.getControl(cid)
            control.reset()
            control.addItems(items)
            return control
        except Exception as exc:
            _log('control %s: %s' % (cid, exc))
            return None

    def _height(self, cid, height):
        try:
            self.getControl(cid).setHeight(int(height))
        except Exception as exc:
            _log('height of %s: %s' % (cid, exc))

    def _step_options(self, group):
        """[(name, values)] a row steps through; the first is Any."""
        model = self.model
        if group in STEPS:
            have = set(model.values[group]) | model.chosen[group]
            return [(_t(ar, en), set(values)) for ar, en, values in STEPS[group]
                    if not values or have & set(values)]
        if group == 'sort':
            return [(name, key) for key, name in self.sorts]
        return [(_t('الكل', 'Any'), set())] + [(_name(group, value, model.names), {value}) for value in model.values[group]]

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        model = self.model
        for key, text in (('title', _t('الفلتر', 'Filter')), ('h.smart', _t('اختيارات ذكية', 'Smart picks')),
                          ('keep', _t('تذكّر الفلتر لكل الأعمال', 'Remember filters')), ('reset', _t('مسح', 'Reset'))):
            self.setProperty('sf.' + key, text)
        self._picks = model.picks()
        items = []
        for key, name, line, _choice, n in self._picks:
            item = xbmcgui.ListItem(name, line, offscreen=True)
            item.setProperty('value', key)
            item.setProperty('tone', TONE.get(key, 'blue'))
            item.setProperty('count', str(n))
            items.append(item)
        self._set(SMART, items)
        self.setProperty('sf.has.smart', '1' if items else '')
        rows = (len(items) + 1) // 2
        self._height(SMART, rows * PILL_STEP)
        self._height(SMART + 100, HEAD + rows * PILL_STEP)
        first = SMART if items else 0
        for cid, groups, columns in PANELS:
            groups = groups if isinstance(groups, tuple) else (groups,)
            group = groups[-1]
            self.setProperty('sf.h.%s' % group, _t(*HEADINGS[group]))
            items = []
            for part in groups:                     # one panel can hold two sections' chips
                for value in model.values[part]:
                    item = xbmcgui.ListItem(_name(part, value, model.names), offscreen=True)
                    item.setProperty('value', value)
                    item.setProperty('group', part)
                    item.setProperty('tone', TONE.get(value, 'grey'))
                    items.append(item)
            self._set(cid, items)
            self.setProperty('sf.has.%s' % group, '1' if items else '')
            rows = (len(items) + columns - 1) // columns
            self._height(cid, rows * CHIP_STEP)
            self._height(cid + 100, HEAD + rows * CHIP_STEP)
            first = first or (cid if items else 0)
        self._steps = []
        for group in ('size', 'rate', 'type', 'addon', 'sort'):
            options = self._step_options(group)
            if len(options) > (1 if group == 'sort' else 2) or (group != 'sort' and model.chosen[group]):
                self._steps.append((group, options))
        items = []
        for group, _options in self._steps:
            item = xbmcgui.ListItem(_t(*ROW_NAMES[group]), offscreen=True)
            item.setProperty('value', group)
            items.append(item)
        self._set(ROWS, items)
        self._height(ROWS, len(items) * ROW_STEP)
        self._height(ROWS + 100, len(items) * ROW_STEP + 8)
        self.refresh()
        self.setFocusId(first or ROWS)

    def refresh(self):
        """Ticks, counts and the totals: nothing is rebuilt, so the cursor stays."""
        model = self.model
        try:
            control = self.getControl(SMART)
            for index in range(control.size()):
                choice = self._picks[index][3]
                same = all(model.chosen[g] == choice.get(g, set()) for g in FILTERS)
                control.getListItem(index).setProperty('on', '1' if same else '')
        except Exception:
            pass
        for cid, _groups, _columns in PANELS:
            try:
                control = self.getControl(cid)
                size = control.size()
            except Exception:
                continue
            for index in range(size):
                item = control.getListItem(index)
                value, group = item.getProperty('value'), item.getProperty('group')
                on = value in model.chosen[group]
                n = model.count(group, value)
                item.setProperty('on', '1' if on else '')
                item.setProperty('count', str(n))
                item.setProperty('dim', '' if n or on else '1')
        try:
            control = self.getControl(ROWS)
            for index, (group, options) in enumerate(self._steps):
                item = control.getListItem(index)
                if group == 'sort':
                    item.setLabel2(dict(self.sorts).get(self.sort, ''))
                    item.setProperty('on', '')
                    continue
                at = next((i for i, (_n, values) in enumerate(options) if values == model.chosen[group]), -1)
                item.setLabel2(options[at][0] if at >= 0 else summary({group: model.chosen[group]}, model.names))
                item.setProperty('on', '1' if model.chosen[group] else '')
        except Exception:
            pass
        total, everything = model.total(), len(model.facts)
        filtered = active(model.chosen)
        self.setProperty('sf.total', str(total))
        self.setProperty('sf.of', _t('من %d مصدر', 'of %d sources') % everything if filtered else _t('مصدر', 'sources'))
        # the line under the title names what is on; each section's heading names its own
        self.setProperty('sf.hint', summary(model.chosen, model.names) if filtered
                         else _t('رتّب مصادرك · يمين يقفل', 'Refine your streams · Right closes'))
        self.setProperty('sf.on', '1' if filtered else '')
        for _cid, groups, _columns in PANELS:
            groups = groups if isinstance(groups, tuple) else (groups,)
            self.setProperty('sf.s.%s' % groups[-1],
                             summary(dict((g, model.chosen[g]) for g in groups), model.names))
        # nothing left: the count turns amber and the main button says so
        self.setProperty('sf.none', '' if total else '1')
        self.setProperty('sf.show', _t('عرض %d مصدر', 'Show %d streams') % total if total
                         else _t('ما بقي شي · خفّف الفلتر', 'Nothing left · ease the filter'))
        self.setProperty('sf.keep.on', '1' if self.keep else '')
        self._mix(total, everything)

    def _mix(self, total, everything):
        """The bar under the head: as long as the share of sources left, split by resolution."""
        counts = self.model.mix()
        left, room = MIX_LEFT, MIX_WIDTH * total / float(everything or 1)
        for index, key in enumerate(('4K', '1080', '720', '')):
            width = int(round(room * counts.get(key, 0) / float(total or 1)))
            try:
                control = self.getControl(MIX + index)
                control.setPosition(left, MIX_TOP)
                control.setWidth(max(0, width))
                control.setVisible(width > 0)
            except Exception:
                return
            left += width

    def _changed(self):
        self.refresh()
        if callable(self.on_change):
            try:
                self.on_change(self.model.chosen, self.sort)
            except Exception as exc:
                _log('the page did not follow: %s' % exc)

    def onClick(self, cid):
        model = self.model
        if cid == SHOW:
            return self.close()
        if cid == KEEP:
            self.keep = not self.keep
            return self.refresh()
        if cid == RESET:
            model.clear()
            return self._changed()
        try:
            at = self.getControl(cid).getSelectedPosition()
        except Exception:
            return
        if cid == SMART:
            if not 0 <= at < len(self._picks):
                return
            choice = self._picks[at][3]
            same = all(model.chosen[g] == choice.get(g, set()) for g in FILTERS)
            model.clear()
            if not same:
                for group, values in choice.items():
                    model.chosen[group] = set(values)
            return self._changed()
        if cid == ROWS:
            if not 0 <= at < len(self._steps):
                return
            group, options = self._steps[at]
            if group == 'sort':
                keys = [key for _n, key in options]
                self.sort = keys[(keys.index(self.sort) + 1) % len(keys)] if self.sort in keys else keys[0]
            else:
                now = next((i for i, (_n, values) in enumerate(options) if values == model.chosen[group]), -1)
                model.chosen[group] = set(options[(now + 1) % len(options)][1])
            return self._changed()
        for panel, _groups, _columns in PANELS:
            if cid == panel:
                item = self.getControl(cid).getListItem(at)
                value, group = item.getProperty('value'), item.getProperty('group')
                if value and group in model.chosen:
                    model.toggle(group, value)
                    self._changed()
                return

    def onFocus(self, cid):
        # Right from a control's last place lands on the hidden button: the panel goes away
        if cid == AWAY:
            self.close()

    def onAction(self, action):
        if action.getId() in A_BACK:
            self.close()


def run(rows, minutes, chosen, sort, sorts, keep, on_change=None, edge=False, anime=False):
    """Open the panel; (choice, sort, keep) as it was left. on_change(choice, sort) is called
    after every press, so the page behind shows the result while the panel is open."""
    import xbmcaddon
    path = xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path')
    _log('%d rows, running time %d min' % (len(rows), minutes))
    window = FilterWindow('dexhub_filter.xml', path, 'Default', '1080i', rows=rows, minutes=minutes,
                          chosen=chosen, sort=sort, sorts=sorts, keep=keep, on_change=on_change, anime=anime)
    try:
        window.doModal()
        return window.model.chosen, window.sort, window.keep
    finally:
        del window
