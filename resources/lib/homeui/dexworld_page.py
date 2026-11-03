# -*- coding: utf-8 -*-
"""DexWorld (v5.10.496): the page of https://blocks.dexworld.cc, as the site draws it.

WindowXMLDialog on dexhub_dexworld*.xml (tools/s482/make_dexworld.py). The
skin's bar opens it (the globe after Discover and Live TV, action
home_dexworld). From the site's JSON (dexworld.py):

- the filters as chips (120): All shows the site's main page (its rows marked
  main), the others every row of their kind, in the site's order;
- on All, the bundle cards (451): OK opens the bundle's rows;
- one section per row: a block as posters (numbered when the site numbers
  it), a collection (kit) as its members' covers: OK on a cover opens the
  collection's rows with that one in focus, as the site's "Show sections";
- OK on a title opens its page; Play plays a film; the last card of a block,
  "See all", opens its whole list as a grid (action dexworld_row);
- Menu on a section: See all, Add to a section of the bar (bar_sections:
  one of yours, or a new one); a collection adds all its blocks.

Back from an opened collection or bundle returns to the filter it came from.
"""
import threading

import xbmc
import xbmcgui

from .. import dexworld as D
from .. import lazy_tb as traceback
from .discover_page import A_BACK, A_LEFT, A_RIGHT, A_UP, A_DOWN, A_CONTEXT, A_MENU, A_PLAY, _item
from .sport_page import take_focus

SLOTS = 28
CHIPS = 120
BUNDLES = 451
LISTS = {'posters': 500, 'ranked': 600, 'wide': 700}
TINTS = ('FF1E3A5F', 'FF4A2545', 'FF1F4D3A', 'FF5A3A1A', 'FF2E2F5E', 'FF4D1F24')


def _t(ar, en):
    return en if D.lang() == 'en' else ar


def _kind_of(row):
    if row.get('kind') == 'kit':
        return 'wide'
    return 'ranked' if row.get('style') == 'ranked' else 'posters'


def _title_row(entry):
    kind = 'series' if entry.get('type') == 'tv' else 'movie'
    return {'kind': kind, 'tmdb': str(entry.get('tmdb') or ''), 'imdb': entry.get('imdb') or '',
            'title': entry.get('title') or '', 'poster': entry.get('poster') or '',
            'fanart': entry.get('backdrop') or entry.get('poster') or '', 'year': entry.get('year') or '',
            'rating': entry.get('rating') or '', 'rank': entry.get('rank')}


class DexWorldWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(DexWorldWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.state = kwargs.get('state') if isinstance(kwargs.get('state'), dict) else {}
        self.state.setdefault('stack', [('filter', 'all')])
        self.data = {}
        self.sections = {}        # n -> (row, kind, [cards])
        self.bundles = []
        self.next_title = None
        self.next_action = None
        self.next_grid = None
        self._lock = threading.Lock()
        self._threads = []
        self._inited = False
        self._closing = False

    # ---------------------------------------------------------- lifecycle
    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('dw.t.title', 'DexWorld')
        self.setProperty('dw.t.hello', 'DexWorld')
        self.setProperty('dw.t.sub', _t('جاري التحميل…', 'Loading…'))
        self.setProperty('dw.b.off', '1')
        for n in range(1, SLOTS + 1):
            self.setProperty('dw.s%d.off' % n, '1')
        try:
            from ..art_tone import Tone
            self.tone = Tone(self)
        except Exception:
            self.tone = None
        self._later(self._load)

    def _later(self, job):
        from .. import profile_scope
        thread = threading.Thread(target=profile_scope.capture(job), name='DexHub-dexworld')
        thread.daemon = True
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()

    def close_window(self):
        self._closing = True
        self.close()

    def on_cover(self):
        pass

    def on_uncover(self):
        pass

    def on_close(self):
        self._closing = True
        try:
            if self.tone:
                self.tone.stop()
        except Exception:
            pass
        for thread in self._threads:
            thread.join(9.0)

    def _load(self):
        try:
            self.data = D.page()
        except Exception:
            xbmc.log('[DexHub] dexworld page: %s' % traceback.format_exc(), xbmc.LOGWARNING)
            self.data = {}
        if self._closing:
            return
        if not self.data.get('rows'):
            self.setProperty('dw.t.sub', _t('تعذر الوصول إلى DexWorld الآن، جرّب بعد قليل',
                                            "DexWorld can't be reached right now, try again shortly"))
            return
        self._render(focus=self.state.get('focus'))

    # ---------------------------------------------------------- drawing
    def _view(self):
        return self.state['stack'][-1]

    def _chips(self, active):
        items = []
        for f in self.data.get('filters') or []:
            label = '%s  %s' % (f.get('title') or '', f.get('count') or '') if f.get('key') != 'all' else (f.get('title') or '')
            items.append(_item(label, '', None, sel='1' if f.get('key') == active else '', key=f.get('key')))
        control = self.getControl(CHIPS)
        control.reset()
        control.addItems(items)
        keys = [f.get('key') for f in self.data.get('filters') or []]
        if active in keys:
            control.selectItem(keys.index(active))

    def _render(self, focus=None):
        what, key = self._view()
        filters = dict((f.get('key'), f) for f in self.data.get('filters') or [])
        if what == 'filter':
            rows = D.filter_rows(self.data, key)
            heading = filters.get(key, {}).get('title') or 'DexWorld'
            sub = _t('%d قسم', '%d sections') % len(rows)
            if key == 'all':
                heading = 'DexWorld'
                sub = _t('أقسام DexWorld كما في الموقع: اختر قسماً، أو أضفه لشريطك من القائمة',
                         'DexWorld sections as on the site: open one, or add it to your bar from the menu')
            active = key
        else:
            rows = D.scope_rows(self.data, key)
            owner = D.rows_by_id(self.data).get(key) or dict((b.get('id'), b) for b in self.data.get('bundles') or []).get(key) or {}
            heading = owner.get('title') or 'DexWorld'
            sub = owner.get('desc') or (_t('%d قسم', '%d sections') % len(rows))
            active = self.state['stack'][0][1]
        with self._lock:
            if self._closing:
                return
            self.setProperty('dw.t.hello', heading)
            self.setProperty('dw.t.sub', sub)
            self.setProperty('dw.t.title', 'DexWorld' if what == 'filter' else 'DexWorld  ›  ' + (filters.get(active, {}).get('title') or ''))
            self._chips(active)
            self._show_bundles(what == 'filter' and key == 'all')
            self.sections = {}
            for n in range(1, SLOTS + 1):
                if n <= len(rows):
                    self._show_section(n, rows[n - 1])
                else:
                    self.setProperty('dw.s%d.off' % n, '1')
            self._wire()
        target = self._focus_target(focus)
        take_focus(self, target)
        self._describe(target)

    def _show_bundles(self, on):
        self.bundles = list(self.data.get('bundles') or []) if on else []
        control = self.getControl(BUNDLES)
        control.reset()
        if not self.bundles:
            self.setProperty('dw.b.off', '1')
            return
        by_id = D.rows_by_id(self.data)
        cards = []
        for index, bundle in enumerate(self.bundles):
            posters = []
            for mid in bundle.get('members') or []:
                for entry in (by_id.get(mid) or {}).get('items') or []:
                    if entry.get('poster') and entry['poster'] not in posters:
                        posters.append(entry['poster'])
                        break
                if len(posters) == 3:
                    break
            cards.append(_item(bundle.get('title') or '', _t('%d قسم', '%d sections') % int(bundle.get('count') or 0),
                               {'thumb': ''}, tint=TINTS[index % len(TINTS)], solo='',
                               **dict(('p%d' % k, posters[k] if k < len(posters) else '') for k in range(3))))
        control.addItems(cards)
        self.setProperty('dw.b.eyebrow', _t('حزم جاهزة', 'Ready bundles'))
        self.setProperty('dw.b.heading', _t('حزم DexWorld', 'DexWorld bundles'))
        self.setProperty('dw.b.off', '')

    def _show_section(self, n, row):
        kind = _kind_of(row)
        cards, items = [], []
        if row.get('kind') == 'kit':
            for member in row.get('members') or []:
                if not isinstance(member, dict):
                    continue
                cards.append({'member': member.get('id'), 'title': member.get('title') or ''})
                items.append(_item(member.get('title') or '', '', {'thumb': member.get('cover') or '', 'fanart': member.get('cover') or ''}))
        else:
            for index, entry in enumerate(row.get('items') or []):
                if not (entry.get('tmdb') or entry.get('imdb')):
                    continue
                title = _title_row(entry)
                cards.append(title)
                art = {'thumb': title['poster'] or title['fanart'], 'fanart': title['fanart']}
                line = ' · '.join(x for x in (str(title['year'] or ''), ('★ %s' % title['rating']) if title['rating'] else '') if x)
                if kind == 'ranked':
                    rank = int(entry.get('rank') or index + 1)
                    items.append(_item(title['title'], line, art,
                                       rank='%s/resources/media/homeui/rank/rank_side_%d.png' % (self.app.addon_path, min(rank, 20))))
                else:
                    items.append(_item(title['title'], line, art))
            if int(row.get('total') or 0) > len(cards):
                cards.append({'all': True})
                items.append(_item(_t('عرض الكل', 'See all'), _t('%s عنوان', '%s titles') % '{:,}'.format(int(row.get('total') or 0)),
                                   {'thumb': row.get('cover') or '', 'fanart': row.get('cover') or ''}))
        self.sections[n] = (row, kind, cards)
        for other, base in LISTS.items():
            if other != kind:
                self.getControl(base + n).reset()
        control = self.getControl(LISTS[kind] + n)
        control.reset()
        control.addItems(items)
        self.setProperty('dw.s%d.kind' % n, kind)
        self.setProperty('dw.s%d.heading' % n, row.get('title') or '')
        eyebrow = row.get('desc') or ''
        if row.get('kind') == 'kit':
            eyebrow = eyebrow or (_t('%d قسم · اضغط على قسم لعرضه', '%d sections · open one to see it') % len(cards))
        self.setProperty('dw.s%d.eyebrow' % n, eyebrow)
        self.setProperty('dw.s%d.src' % n, row.get('meta') or '')
        self.setProperty('dw.s%d.logo' % n, row.get('logo') or '' if row.get('style') == 'platform' else '')
        self.setProperty('dw.s%d.off' % n, '' if items else '1')

    # ---------------------------------------------------------- moving
    def _list_of(self, n):
        return LISTS[self.sections[n][1]] + n

    def _number_of(self, cid):
        for base in LISTS.values():
            if base < cid <= base + SLOTS:
                return cid - base
        return 0

    def _chain(self):
        chain = [BUNDLES] if self.bundles else []
        return chain + [self._list_of(n) for n in sorted(self.sections) if self.sections[n][2]]

    def _wire(self):
        try:
            chain = self._chain()
            previous = CHIPS
            self.getControl(CHIPS).controlDown(self.getControl(chain[0] if chain else CHIPS))
            for index, cid in enumerate(chain):
                control = self.getControl(cid)
                control.controlUp(self.getControl(previous))
                control.controlDown(self.getControl(chain[index + 1] if index + 1 < len(chain) else cid))
                previous = cid
        except (RuntimeError, IndexError, TypeError):
            pass

    def _focus_target(self, focus):
        """focus: ('member', id) a section by its row id; None the first section."""
        if focus:
            for n, (row, _kind, cards) in self.sections.items():
                if cards and row.get('id') == focus:
                    return self._list_of(n)
        chain = self._chain()
        return chain[0] if chain and self._view()[0] == 'scope' else CHIPS

    def _card_at(self, cid):
        n = self._number_of(cid)
        if not n or n not in self.sections:
            return 0, None
        cards = self.sections[n][2]
        try:
            pos = self.getControl(cid).getSelectedPosition()
        except RuntimeError:
            return n, None
        return n, (cards[pos] if 0 <= pos < len(cards) else None)

    def _describe(self, cid):
        n, card = self._card_at(cid)
        if not card:
            return
        if card.get('member'):
            row = D.rows_by_id(self.data).get(card['member']) or {}
            entry = ((row.get('items') or [{}])[0]) if row.get('items') else {}
            fanart = entry.get('backdrop') or row.get('cover') or ''
            poster = entry.get('poster') or ''
        else:
            fanart, poster = card.get('fanart') or card.get('poster') or '', card.get('poster') or ''
        if fanart:
            self.setProperty('dw.f.fanart', fanart)
        if self.tone and poster:
            self.tone.follow(poster)

    # ---------------------------------------------------------- keys
    def onAction(self, action):
        aid = action.getId()
        focus = self.getFocusId()
        if aid in A_BACK:
            if len(self.state['stack']) > 1:
                back_to = self.state['stack'].pop()
                self._render(focus=back_to[1])
            else:
                self.close_window()
            return
        if focus == CHIPS and aid in (A_LEFT, A_RIGHT):
            self._pick_filter()
            return
        if self._number_of(focus):
            if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
                self._describe(focus)
            elif aid in (A_CONTEXT, A_MENU):
                self._menu(focus)
            elif aid == A_PLAY:
                _n, card = self._card_at(focus)
                if card and card.get('tmdb'):
                    from .discover_page import DiscoverWindow
                    DiscoverWindow._play(self, card)

    def onFocus(self, cid):
        if self._number_of(cid):
            self._describe(cid)

    def _pick_filter(self):
        try:
            pos = self.getControl(CHIPS).getSelectedPosition()
            key = (self.data.get('filters') or [])[pos].get('key')
        except (RuntimeError, IndexError, AttributeError):
            return
        if self.state['stack'] == [('filter', key)]:
            return
        self.state['stack'] = [('filter', key)]
        self._render()

    def onClick(self, cid):
        if cid == CHIPS:
            self._pick_filter()
            chain = self._chain()
            if chain:
                take_focus(self, chain[0])
            return
        if cid == BUNDLES:
            try:
                bundle = self.bundles[self.getControl(BUNDLES).getSelectedPosition()]
            except (RuntimeError, IndexError):
                return
            self.state['stack'].append(('scope', bundle.get('id')))
            self._render()
            return
        n, card = self._card_at(cid)
        if not card:
            return
        row = self.sections[n][0]
        if card.get('member'):
            self.state['stack'].append(('scope', row.get('id')))
            self._render(focus=card['member'])
        elif card.get('all'):
            self._see_all(row)
        else:
            self.state['focus'] = row.get('id')
            self.next_title = {'m': card['kind'], 'title': card['title'], 'tmdb': card['tmdb'], 'imdb': card['imdb']}
            self.close_window()

    def _see_all(self, row):
        self.state['focus'] = row.get('id')
        self.next_grid = ({'action': 'dexworld_row', 'id': row.get('id') or '', 'title': row.get('title') or ''},
                          row.get('title') or '')
        self.close_window()

    def _open(self, row):
        # DiscoverWindow._play's way to a title page
        self.next_title = {'m': row.get('kind'), 'title': row.get('title') or '', 'tmdb': row.get('tmdb') or '',
                           'imdb': row.get('imdb') or ''}
        self.close_window()

    # ---------------------------------------------------------- menu
    def _menu(self, cid):
        n, _card = self._card_at(cid)
        if not n:
            return
        row = self.sections[n][0]
        blocks = D.scope_rows(self.data, row.get('id')) if row.get('kind') == 'kit' else [row]
        choices = []
        if row.get('kind') == 'kit':
            choices.append(('open', _t('اعرض الأقسام (%d)', 'Show the sections (%d)') % len(blocks)))
        else:
            choices.append(('all', _t('عرض الكل', 'See all')))
        choices.append(('add', _t('أضف إلى قسم في الشريط…', 'Add to a bar section…')))
        pick = xbmcgui.Dialog().select(row.get('title') or 'DexWorld', [label for _key, label in choices])
        if pick < 0:
            return
        key = choices[pick][0]
        if key == 'open':
            self.state['stack'].append(('scope', row.get('id')))
            self._render()
        elif key == 'all':
            self._see_all(row)
        else:
            self._add(row, blocks)

    def _add(self, row, blocks):
        from .. import bar_sections as B
        custom = B.read().get('custom') or []
        names = [c.get('name') or '' for c in custom]
        options = names + ([_t('قسم جديد…', 'A new section…')] if len(custom) < len(B.CUSTOM) else [])
        if not options:
            return
        pick = xbmcgui.Dialog().select(_t('أضف %d قسم إلى', 'Add %d rows to') % len(blocks), options)
        if pick < 0:
            return
        if pick < len(custom):
            done = D.add_to_section(blocks, slot=pick)
        else:
            name = xbmcgui.Dialog().input(_t('اسم القسم', 'Section name'), row.get('title') or 'DexWorld')
            if not name:
                return
            done = D.add_to_section(blocks, name=name)
        if done:
            xbmcgui.Dialog().notification('DexWorld', _t('انضاف إلى «%s»', 'Added to "%s"') % done, time=2500, sound=False)


def open_dexworld(app):
    """The page; a title or a block's whole list shows over the Home and the page comes back after a list."""
    from . import ui_size
    from .app import _details_tile
    state = {}
    while True:
        window = DexWorldWindow(ui_size.xml('dexhub_dexworld.xml'), app.addon_path, 'Default', '1080i', app=app, state=state)
        app._show(window)
        query, action, grid = window.next_title, window.next_action, window.next_grid
        del window
        if grid:
            route, title = grid
            try:
                app.open_grid(route, title=title)
            except Exception:
                xbmc.log('[DexHub] dexworld: grid failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
                return
            continue
        if action:
            xbmc.executebuiltin('RunPlugin("%s")' % action)
            return
        if not query:
            return
        try:
            from ..skinui.items import title_here, title_target
            skin_page = title_here()
        except Exception:
            skin_page, title_target = False, None
        if skin_page and title_target is not None:
            xbmc.executebuiltin('RunPlugin("%s")' % title_target(query))
            return
        try:
            app.open_details(_details_tile(query))
        except Exception:
            xbmc.log('[DexHub] dexworld: title page failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
            return
