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
_SORT_LABEL = {'default': ('افتراضي', 'Default'), 'size': ('الحجم', 'Size'), 'quality': ('الجودة', 'Quality')}
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


def _quality(text):
    from ..source_browser import _badge_quality
    return _badge_quality({}, text)


def sources(params, handle):
    entries = []
    if xbmc.Player().isPlayingVideo():
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
            li = xbmcgui.ListItem(label=_t('جاري البحث في كل المصادر…'),
                                  label2=line, offscreen=True)
            li.setProperties({'pos': 'busy', 'rescan': 'true', 'busy': 'true', 'isfolder': 'false'})
            entries.append((C.url('switch_source', pick='none'), li, False))
        elif state == 'done:0':
            li = xbmcgui.ListItem(label=_t('ما لقينا مصادر لهذا العمل'),
                                  label2=_t('جرّب بعدين أو تأكد من إضافاتك'), offscreen=True)
            li.setProperties({'pos': 'none', 'rescan': 'true', 'isfolder': 'false'})
            entries.append((C.url('switch_source', pick='none'), li, False))
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
        li = xbmcgui.ListItem(label=('الترتيب: %s' if arabic else 'Sort: %s') % label,
                              label2=' / '.join(_SORT_LABEL[m][0 if arabic else 1] for m in _SORTS),
                              offscreen=True)
        li.setProperties({'pos': 'sort', 'sort': mode, 'isfolder': 'false'})
        entries.append((C.url('skin_osd_sources_sort'), li, False))
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
        for pos, row in enumerate(listed[:60]):
            name = _TAGS.sub('', str(row.get('l') or '?')).replace('\n', ' ').strip()
            name = re.sub(r'\s{2,}', '  ', name)
            if row.get('n'):
                # v5.10.297: as Nuvio shows it: the add-on's own name line on
                # top, its description under it, and the size
                name = _plain(row.get('n')) or name
                desc = [_plain(part) for part in str(row.get('d') or '').splitlines()]
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
            li = xbmcgui.ListItem(label=name, label2=line, offscreen=True)
            if row.get('current'):
                props['iscurrent'] = 'true'
            props.update(_island_props(name, line, props.get('quality') or ''))
            li.setProperties(props)
            entries.append((C.url('switch_source', stream_key=str(row.get('k') or ''), item=item), li, False))
        # v5.10.248: the whole search again, over the playing video (the list
        # holds only what was found before the first source was picked)
        try:
            from ..i18n import tr
        except Exception:
            tr = lambda text: text
        if not state.startswith('searching'):
            li = xbmcgui.ListItem(label=tr('ابحث عن مصادر من جديد'),
                                  label2=tr('بحث كامل في كل المصادر، والنتائج تطلع هنا'), offscreen=True)
            li.setProperties({'pos': 'rescan', 'rescan': 'true', 'isfolder': 'false'})
            entries.append((C.url('switch_source', rescan='1'), li, False))
    if handle >= 0:
        if entries:
            xbmcplugin.addDirectoryItems(handle, entries, len(entries))
        xbmcplugin.endOfDirectory(handle, succeeded=True, updateListing=False, cacheToDisc=False)
