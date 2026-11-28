# -*- coding: utf-8 -*-
"""The player's Sources panel (v5.10.243, skin.dexhub 3.28.12).

skin_osd_sources lists the sources of the playing title in the player's own
style (Custom_1149_OSD_Sources.xml): the list Dex Hub found the last time
the title's sources were shown, the one playing first. OK on a row runs
switch_source with its place in this list (pick=<n>), which plays it from
the same point; nothing is searched again.
"""
import json
import re

import xbmc
import xbmcgui
import xbmcplugin

from . import common as C

_TAGS = re.compile(r'\[/?(?:COLOR|B|I|UPPERCASE|LOWERCASE|CAPITALIZE)[^\]]*\]', re.I)
_QUALITY = (('2160', '4K'), ('4k', '4K'), ('uhd', '4K'), ('1080', '1080p'), ('720', '720p'),
            ('480', 'SD'), ('sd', 'SD'))


def _item_key(canonical_id, season, episode):
    def _n(value):
        text = str(value).strip() if value is not None else ''
        return str(int(text)) if text.isdigit() else text
    return '%s|%s|%s' % (_n(canonical_id), _n(season), _n(episode))


def rows():
    """(rows, has_current) in the order switch_source shows them."""
    try:
        from ..playback.session import load_session
        ctx = load_session() or {}
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict):
        ctx = {}
    want = _item_key(ctx.get('canonical_id'), ctx.get('season'), ctx.get('episode')) \
        if (ctx.get('canonical_id') or ctx.get('video_id')) else ''
    found = []
    data = {}
    try:
        data = json.loads(C.home().getProperty('dexhub.last_streams') or '{}')
        if data.get('rows') and (not want or data.get('item') == want):
            found = [r for r in data['rows'] if r.get('k')]
    except Exception:
        found = []
    if not found and want:
        try:
            from .. import cache_store
            cached = cache_store.get('source_index', want) or {}
            if cached.get('item') == want:
                found = [r for r in cached.get('rows') or [] if r.get('k')]
        except Exception:
            found = []
    if not found and isinstance(data, dict) and data.get('rows'):
        C.log('player sources: a list for %s, the playing title is %s'
              % (data.get('item'), want), xbmc.LOGDEBUG)
    key = str(ctx.get('stream_key') or '')
    for i, row in enumerate(found):
        if key and str(row.get('k') or '') == key:
            return [dict(row, current=True)] + found[:i] + found[i + 1:], True
    return found, False


_STATE = 'dexhub.osd.search'


def _state():
    """The search state while it is fresh: a search that ended a minute ago
    no longer speaks in the panel, and one stuck for two minutes is over."""
    import time
    raw = C.home().getProperty(_STATE) or ''
    state, _sep, stamp = raw.partition('|')
    try:
        age = time.time() - float(stamp or 0)
    except ValueError:
        age = 1e9
    if state.startswith('searching') and age < 120:
        return state
    if state.startswith('done:') and age < 60:
        return state
    return ''


def searching():
    return _state().startswith('searching')


def set_state(state):
    """v5.10.252: the panel's search state ('searching', 'done:<n>', '')
    and a reload of the open panel's list."""
    import time
    C.home().setProperty(_STATE, ('%s|%d' % (state, time.time())) if state else '')
    stamp = '%d' % (time.time() * 1000)
    # the skin's custom dialog 1149 is Kodi's window 11149; only while it
    # is open (Kodi logs "Window id does not exist" for a closed one)
    if xbmc.getCondVisibility('Window.IsVisible(1149)'):
        try:
            xbmcgui.Window(11149).setProperty('UID', stamp)
        except Exception:
            pass
    C.home().setProperty('dexhub.osd.rev', stamp)


# v5.10.297: Nuvio-style rows. Kodi's fonts draw no colour emoji, so the
# add-on's pictographs are dropped and its words, dots and numbers kept.
_EMOJI = re.compile('[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3\U000E0000-\U000E007F]')
_SORT = 'dexhub.osd.sources.sort'
_SORTS = ('default', 'size', 'quality')
_SORT_LABEL = {'default': ('ترتيب المصدر', 'Source order'), 'size': ('الحجم', 'Size'), 'quality': ('الجودة', 'Quality')}
_RANK = {'4K': 4, '1080p': 3, '720p': 2, 'SD': 1}


def _plain(text):
    text = _TAGS.sub('', _EMOJI.sub('', str(text or '')))
    text = re.sub(r'(^|\s)[\u00b7|]\s*(?=[\u00b7|]|$)', ' ', text)
    return re.sub(r'[ \t]{2,}', ' ', text).strip(' \u00b7|')


def _bytes(size):
    match = re.search(r'([\d.]+)\s*([KMGT])I?B', str(size or ''), re.I)
    if not match:
        return 0
    return float(match.group(1)) * 1024 ** ' KMGT'.index(match.group(2).upper())


_RES_ONLY = re.compile(r'^(?:\d{3,4}[pi]|[48]k|uhd|fhd|hd|sd)$', re.I)


def _without_repeats(line, name, size):
    """v5.10.615: the card's line without what the card shows already: the size (on its right)
    and a part the name holds ("86.17 GB · 324d · altHUB" under "Usenet altHUB")."""
    parts = [p.strip() for p in str(line or '').split('\u00b7') if p.strip()]
    low_name = str(name or '').casefold()
    low_size = str(size or '').replace(' ', '').casefold()
    kept = []
    for part in parts:
        folded = part.casefold()
        if low_size and folded.replace(' ', '') == low_size:
            continue
        if len(folded) >= 3 and folded in low_name:
            continue
        kept.append(part)
    return '  \u00b7  '.join(kept)


_QTONE = {'4K': 'FFF5B428', '8K': 'FFF5B428', '1080p': 'FF50C8FF', '720p': 'FF6EE7A8'}


def _island_props(name, line, quality):
    """v5.10.307: what the player's sources island draws under the name: the
    description as lines (the sources page's way), how many of them the
    focused card needs, the quality's colour, and the elite badges with the
    packed origins the sources page uses."""
    out = {}
    parts = [p.strip() for p in str(line or '').split('\u00b7') if p.strip()]
    lines = []
    for part in parts:
        if lines and len(lines[-1]) + len(part) + 3 <= 68:
            lines[-1] = lines[-1] + '  \u00b7  ' + part
        else:
            lines.append(part)
    lines = lines[:4] or ['']
    out['nv_desc'] = '[CR]'.join(lines)
    out['nv_fh'] = str(len(lines))
    out['qtone'] = _QTONE.get(quality, 'FF9AA3B2' if quality else '')
    # List-item XML does not reliably resolve a property-valued left origin.
    # Encode the reserved quality width in the existing fixed slide bits.
    quality_width = ((14 * max(2, len(quality)) + 19) // 4) * 4 if quality else 0
    out['nv_qw'] = '0'
    try:
        from .. import source_browser as sb
        layout = sb._source_badge_layout(sb._elite_badge_images(
            {'name': name, 'label2': line, 'quality': quality}, max_items=7,
            skip_resolution=bool(quality)))
        moves = sb._source_badge_moves(layout, start=quality_width)
        for idx, (image, shape) in enumerate(layout, 1):
            out['elite_badge%d' % idx] = image
            out['elite_w%d' % idx] = str(int(int(shape[1:]) * sb.SOURCE_BADGE_HEIGHT / 4.0))
            # v5.10.322: the set's border colour, a frame 4 px around the image
            out['elite_frame%d' % idx] = sb.badge_frame(image)
            out['elite_fw%d' % idx] = str(int(int(shape[1:]) * sb.SOURCE_BADGE_HEIGHT / 4.0) + 8)
            out['elite_move%d' % idx] = moves[idx - 1]
    except Exception:
        pass
    return out


def sort_mode():
    mode = C.home().getProperty(_SORT) or 'default'
    return mode if mode in _SORTS else 'default'


def cycle_sort(params=None):
    """skin_osd_sources_sort: Default, Size, Quality, and the list reloads."""
    import time
    mode = _SORTS[(_SORTS.index(sort_mode()) + 1) % len(_SORTS)]
    C.home().setProperty(_SORT, mode)
    stamp = '%d' % (time.time() * 1000)
    try:
        xbmcgui.Window(11149).setProperty('UID', stamp)     # the open panel (it asked for this)
    except Exception:
        pass


# v5.10.448: the sources page's quality and cached filters, as a row under
# the sort row (OK steps to the next filter that has rows)
_FILTER = 'dexhub.osd.sources.filter'
_FILTER_COUNTS = 'dexhub.osd.sources.counts'
_FILTERS = ('all', '4K', '1080p', '720p', 'SD', 'cached')
_FILTER_LABEL = {'all': ('الكل', 'All'), '4K': ('4K', '4K'), '1080p': ('1080p', '1080p'), '720p': ('720p', '720p'),
                 'SD': ('SD', 'SD'), 'cached': ('جاهز', 'Cached')}


def filter_mode():
    mode = C.home().getProperty(_FILTER) or 'all'
    return mode if mode in _FILTERS else 'all'


def _matches(props, mode):
    if mode == 'all':
        return True
    if mode == 'cached':
        return props.get('cache') == 'cached'
    return (props.get('quality') or 'SD') == mode


def cycle_filter(params=None):
    """skin_osd_sources_filter: the next filter with rows (All always)."""
    import time
    try:
        counts = json.loads(C.home().getProperty(_FILTER_COUNTS) or '{}')
    except ValueError:
        counts = {}
    start = _FILTERS.index(filter_mode())
    mode = 'all'
    for step in range(1, len(_FILTERS)):
        option = _FILTERS[(start + step) % len(_FILTERS)]
        if option == 'all' or counts.get(option):
            mode = option
            break
    C.home().setProperty(_FILTER, mode)
    try:
        xbmcgui.Window(11149).setProperty('UID', '%d' % (time.time() * 1000))
    except Exception:
        pass


def _quality(text):
    from ..source_browser import _badge_quality
    return _badge_quality({}, text)


def _youtube_rows():
    """(5.10.497) A YouTube video: its qualities, the playing one first marked."""
    from .. import youtube_feed as Y
    try:
        from ..i18n import is_english
        english = is_english()
    except Exception:
        english = False
    entries = []
    for index, label, detail, current in Y.qualities():
        li = xbmcgui.ListItem(label=label, label2=('YouTube  ·  %s' % detail) if detail else 'YouTube', offscreen=True)
        height = int((re.match(r'\d+', label) or re.match('', '')).group(0) or 0)
        quality = ('4K' if height >= 2160 else '1080p' if height >= 1080 else '720p' if height >= 720 else
                   'SD' if height else '')
        props = {'pos': str(len(entries)), 'provider': 'YouTube', 'quality': quality, 'isfolder': 'false'}
        if current:
            props['iscurrent'] = 'true'
        props.update(_island_props(label, li.getLabel2(), quality))
        li.setProperties(props)
        entries.append((C.url('switch_source', yt_stream=str(index)), li, False))
    if not entries:
        li = xbmcgui.ListItem(label='Quality is chosen automatically' if english else 'الجودة تتحدد تلقائياً',
                              label2='YouTube', offscreen=True)
        li.setProperties({'pos': 'none', 'isfolder': 'false'})
        entries.append((C.url('switch_source', pick='none'), li, False))
    return entries


class _Rec(object):
    """A row of the panel as data: what a Kodi list item is made from."""

    def __init__(self, label='', label2='', offscreen=True, props=None):
        self.label, self.label2, self.props = label, label2, dict(props or {})

    def setProperties(self, props):
        self.props.update(props)

    def getLabel2(self):
        return self.label2


_BUILT = 'dexhub.osd.sources.built'
_HEADER = 'dexhub.osd.sources.chips'


def _header_buttons():
    """The skin's panel draws Sort, Filter and Search again as buttons over the list
    (skin.dexhub 3.28.242 sets this when the panel opens): those rows leave the list,
    whose rows are all one height, and the searching / nothing found / search again
    rows are kept only while there is no source to show."""
    return bool(C.home().getProperty(_HEADER))


def _publish_header(**values):
    home = C.home()
    for name, value in values.items():
        prop = 'dexhub.osd.sources.' + name
        if home.getProperty(prop) != value:
            home.setProperty(prop, value)


def _panel_key():
    """What the panel's rows follow: the list found, the search's state, the sort and filter, the
    playing source and title, the language."""
    import hashlib
    try:
        from ..playback.session import load_session
        ctx = load_session() or {}
    except Exception:
        ctx = {}
    try:
        from ..settings_cache import cached_addon
        language = cached_addon().getSetting('ui_language') or ''
    except Exception:
        language = ''
    parts = [C.home().getProperty('dexhub.last_streams') or '', _state(), sort_mode(), filter_mode(),
             str(ctx.get('stream_key') or ''), _item_key(ctx.get('canonical_id'), ctx.get('season'), ctx.get('episode')),
             language, '1' if _header_buttons() else '']
    return hashlib.sha1('\x00'.join(parts).encode('utf-8', 'replace')).hexdigest()


def _panel_rows(build=True):
    """v5.10.615: the panel's rows kept while what they follow is the same: building them took a
    second each opening on a CoreELEC box (the sources page's code and 60 cards of badges); the
    service builds them a few seconds into the film (warm), so the first opening is quick too."""
    key = _panel_key()
    try:
        kept = json.loads(C.home().getProperty(_BUILT) or '{}')
    except ValueError:
        kept = {}
    if kept.get('key') == key and isinstance(kept.get('rows'), list):
        return [(url, _Rec(label, label2, props=props)) for url, label, label2, props in kept['rows']]
    if not build:
        return None
    rows_ = _build_panel()
    try:
        C.home().setProperty(_BUILT, json.dumps({'key': key, 'rows': [[url, r.label, r.label2, r.props]
                                                                       for url, r in rows_]}))
    except Exception:
        pass
    return rows_


def warm():
    """The service, a few seconds into a film: the panel's rows ready before it is opened."""
    try:
        playing_path = xbmc.getInfoLabel('Player.FilenameAndPath') or ''
        if not xbmc.Player().isPlayingVideo() or 'youtube' in playing_path:
            return
        if _panel_rows(build=False) is None:
            _panel_rows()
            C.log('player sources panel built ahead', xbmc.LOGDEBUG)
    except Exception as exc:
        C.log('player sources panel ahead: %s' % exc, xbmc.LOGDEBUG)


def _build_panel():
    """The panel's rows as (url, row) pairs, row a _Rec (no Kodi list item: the service
    builds them too, v5.10.615)."""
    entries = []
    try:
        from ..i18n import tr as _t
    except Exception:
        _t = lambda text: text
    state = _state()
    if state.startswith('searching'):
        # 'searching' or 'searching:<answered>/<providers>'
        counter = state.partition(':')[2]
        line = _t('الفيديو يكمل، والنتائج تطلع هنا')
        if '/' in counter:
            done, _sl, total = counter.partition('/')
            line = _t('ردّ %s من %s') % (done, total) + '  ·  ' + line
        li = _Rec(label=_t('جاري البحث في كل المصادر…'),
                              label2=line, offscreen=True)
        li.setProperties({'pos': 'busy', 'rescan': 'true', 'busy': 'true', 'isfolder': 'false'})
        entries.append((C.url('switch_source', pick='none'), li))
    elif state == 'done:0':
        li = _Rec(label=_t('ما لقينا مصادر لهذا العمل'),
                              label2=_t('جرّب بعدين أو تأكد من إضافاتك'), offscreen=True)
        li.setProperties({'pos': 'none', 'rescan': 'true', 'isfolder': 'false'})
        entries.append((C.url('switch_source', pick='none'), li))
    listed, _current = rows()
    from ..playback.session import load_session
    ctx = load_session() or {}
    item = _item_key(ctx.get('canonical_id'), ctx.get('season'), ctx.get('episode'))
    # v5.10.297: the sort row, then the list in that order (the playing
    # source stays first, as before)
    mode = sort_mode()
    try:
        from ..settings_cache import cached_addon
        arabic = (cached_addon().getSetting('ui_language') or '').strip().lower().startswith('ar')
    except Exception:
        arabic = False
    label = _SORT_LABEL[mode][0 if arabic else 1]
    li = _Rec(label=('الترتيب: %s' if arabic else 'Sort: %s') % label,
                          label2=' / '.join(_SORT_LABEL[m][0 if arabic else 1] for m in _SORTS),
                          offscreen=True)
    li.setProperties({'pos': 'sort', 'sort': mode, 'isfolder': 'false'})
    entries.append((C.url('skin_osd_sources_sort'), li))
    if mode != 'default' and listed:
        head = [r for r in listed[:1] if r.get('current')]
        rest = listed[len(head):]
        if mode == 'size':
            rest = sorted(rest, key=lambda r: -_bytes(r.get('s')))
        else:
            rest = sorted(rest, key=lambda r: (-_RANK.get(_quality(str(r.get('q') or '')) or
                                                          _quality(str(r.get('n') or r.get('l') or '')), 0),
                                               -_bytes(r.get('s'))))
        listed = head + rest
    built = []
    for pos, row in enumerate(listed):
        name = _TAGS.sub('', str(row.get('l') or '?')).replace('\n', ' ').strip()
        name = re.sub(r'\s{2,}', '  ', name)
        if row.get('n'):
            # v5.10.297: as Nuvio shows it: the add-on's own name line on
            # top, its description under it, and the size
            name_lines = [_plain(part) for part in str(row.get('n') or '').splitlines()]
            name_lines = [part for part in name_lines if part]
            name = name_lines[0] if name_lines else name
            # v5.10.615: the add-on's name kept to one line (a second line, "2160p", made the
            # card taller than its place and it covered the next one)
            extra = [part for part in name_lines[1:] if not _RES_ONLY.match(part)]
            desc = extra + [_plain(part) for part in str(row.get('d') or '').splitlines()]
            size = str(row.get('s') or '').strip()
            if size == '--':
                size = ''
            line = '  \u00b7  '.join(part for part in desc if part)
            low = (name + ' ' + line).lower()
            props = {'pos': str(pos), 'provider': _plain(row.get('p') or ''),
                     'quality': _quality(str(row.get('q') or '')) or _quality(name + ' ' + line),
                     'isfolder': 'false'}
            if size:
                props['size'] = size
            if row.get('lib') or row.get('c') is True:
                props['cache'] = 'cached'
            elif row.get('c') is False:
                props['cache'] = 'uncached'
        elif 'id' in row:
            # v5.10.294: the sources window's own line (the add-on's or
            # AIOStreams formatter identity, not the add-on's name)
            ident = _TAGS.sub('', str(row.get('id') or '')).replace('\n', ' ').strip()
            size = str(row.get('s') or '').strip()
            if size == '--':
                size = ''
            # v5.10.296: the add-on's name first, exactly as the sources
            # window shows it, then that window's identity line and size
            addon_name = _TAGS.sub('', str(row.get('p') or '')).replace('\n', ' ').strip()
            parts = [addon_name] if addon_name else []
            if ident and ident.casefold() != addon_name.casefold():
                parts.append(ident)
            if size:
                parts.append(size)
            line = '  ·  '.join(parts)
            low = (name + ' ' + line).lower()
            props = {'pos': str(pos), 'provider': addon_name or ident,
                     'quality': _quality(str(row.get('q') or '')) or _quality(name + ' ' + line),
                     'isfolder': 'false'}
            if size:
                props['size'] = size
            if row.get('lib') or row.get('c') is True:
                props['cache'] = 'cached'
            elif row.get('c') is False:
                props['cache'] = 'uncached'
        else:
            line = _TAGS.sub('', str(row.get('l2') or '')).strip()
            parts = [p.strip() for p in line.split('·') if p.strip()]
            low = (name + ' ' + line).lower()
            props = {'pos': str(pos), 'provider': parts[0] if parts else '',
                     'quality': _quality(name + ' ' + line), 'isfolder': 'false'}
            size = next((p for p in parts if re.search(r'\d\s*(?:gb|mb)\b', p, re.I)), '')
            if size:
                props['size'] = size
            if 'uncached' in low or 'not ready' in low:
                props['cache'] = 'uncached'
            elif 'cached' in low or '⚡' in name or 'ready' in low or 'in library' in low:
                props['cache'] = 'cached'
        built.append((row, name, line, props))
    # v5.10.448: the filter row, with each filter's count
    counts = dict((option, sum(1 for b in built if _matches(b[3], option))) for option in _FILTERS)
    C.home().setProperty(_FILTER_COUNTS, json.dumps(counts))
    shown_mode = filter_mode() if counts.get(filter_mode()) else 'all'
    if built:
        choices = [option for option in _FILTERS if option == 'all' or counts.get(option)]
        li = _Rec(
            label=('الفلتر: %s' if arabic else 'Filter: %s') % _FILTER_LABEL[shown_mode][0 if arabic else 1],
            label2=' / '.join('%s %d' % (_FILTER_LABEL[o][0 if arabic else 1], counts[o]) for o in choices),
            offscreen=True)
        li.setProperties({'pos': 'sort', 'filter': shown_mode, 'isfolder': 'false'})
        entries.append((C.url('skin_osd_sources_filter'), li))
    chosen = [b for b in built if b[0].get('current') or _matches(b[3], shown_mode)]
    # v5.10.615: the header counts sources only ("3 / 63" counted the sort and filter rows),
    # and says how many there are when the list holds the first 60
    C.home().setProperty('dexhub.osd.sources.total', str(min(len(chosen), 60)) if len(chosen) <= 60 else
                         '60  \u00b7  %d' % len(chosen))
    for number, (row, name, line, props) in enumerate(chosen[:60], 1):
        line = _without_repeats(line, name, props.get('size'))
        li = _Rec(label=name, label2=line, offscreen=True)
        props['num'] = str(number)
        if row.get('current'):
            props['iscurrent'] = 'true'
        props.update(_island_props(name, line, props.get('quality') or ''))
        li.setProperties(props)
        entries.append((C.url('switch_source', stream_key=str(row.get('k') or ''), item=item), li))
    # v5.10.248: the whole search again, over the playing video (the list
    # holds only what was found before the first source was picked)
    try:
        from ..i18n import tr
    except Exception:
        tr = lambda text: text
    if not state.startswith('searching'):
        li = _Rec(label=tr('ابحث عن مصادر من جديد'),
                              label2=tr('بحث كامل في كل المصادر، والنتائج تطلع هنا'), offscreen=True)
        li.setProperties({'pos': 'rescan', 'rescan': 'true', 'isfolder': 'false'})
        entries.append((C.url('switch_source', rescan='1'), li))
    return _for_header(entries, state, len(chosen), tr)


def _for_header(entries, state, count, tr):
    """skin.dexhub 3.28.242's panel: Sort, Filter and Search again are its buttons (their
    labels published here), the list holds the sources, or one card while it has none:
    searching, or search again (saying nothing was found after a search found nothing)."""
    searching = state.startswith('searching')
    by_pos = dict((row.props.get('pos'), (url, row)) for url, row in entries
                  if row.props.get('pos') in ('busy', 'none', 'rescan'))
    labels = [row.label for _url, row in entries if row.props.get('pos') == 'sort']
    counter = state.partition(':')[2]
    if searching and '/' in counter:
        done, _sl, total = counter.partition('/')
        stateline = tr('ردّ %s من %s') % (done, total)
    else:
        stateline = tr('جاري البحث في كل المصادر…') if searching else ''
    _publish_header(n=str(count), state='searching' if searching else '', stateline=stateline,
                    sortlabel=labels[0] if labels else '',
                    filterlabel=labels[1] if len(labels) > 1 else '',
                    rescanlabel=tr('ابحث عن مصادر من جديد'))
    if not _header_buttons():
        return entries
    sources_ = [(url, row) for url, row in entries
                if row.props.get('pos') not in ('busy', 'none', 'rescan', 'sort')]
    if sources_:
        return sources_
    if searching:
        return [by_pos['busy']] if 'busy' in by_pos else []
    if 'rescan' not in by_pos:
        return []
    url, row = by_pos['rescan']
    if 'none' in by_pos:
        # nothing found: said on the card that searches again
        row = _Rec(label=by_pos['none'][1].label, label2=row.label2, props=row.props)
    return [(url, row)]


def sources(params, handle):
    entries = []
    # v5.10.615: YouTube told by the playing path (youtube_feed.playing's test) without loading
    # that module (0.23 s of each opening of the panel on a CoreELEC box)
    playing_path = xbmc.getInfoLabel('Player.FilenameAndPath') or ''
    if xbmc.Player().isPlayingVideo() and ('action=youtube_play' in playing_path or 'youtube/manifest' in playing_path):
        _publish_header(n='0', state='', stateline='')     # a YouTube video: no sort, filter or search
        entries = _youtube_rows()
    elif xbmc.Player().isPlayingVideo():
        for url, row in _panel_rows():
            li = xbmcgui.ListItem(label=row.label, label2=row.label2, offscreen=True)
            li.setProperties(row.props)
            entries.append((url, li, False))
    if handle >= 0:
        if entries:
            xbmcplugin.addDirectoryItems(handle, entries, len(entries))
        xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=False)
