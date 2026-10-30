# -*- coding: utf-8 -*-
"""The user's own arrangement of the Dex Hub Home rows (v5.10.98).

Each page ('all' is the Home, then 'movie' and 'series') keeps, in
<profile>/homeui/layout.json:

  order   the row keys in the order the user put them;
  hidden  row key -> true (hidden) or false (shown on purpose, for a row
          that starts hidden such as Next Up);
  custom  rows the user added: a Dex Hub listing (a source catalog, a Plex,
          Emby, Jellyfin or Silo library view) or a collection.

Row keys are the Home's own ('continue', 'nextup', 'cat:...', 'col:...'), and
'my:<id>' for added rows. A row the saved order does not know yet (a catalog
added to Nuvio later) is placed right after the row it follows in the Home's
own order, so nothing new is buried at the bottom.

This module never touches the GUI; the editor is layout_window.py.
"""
from .. import profile_scope as _scope
import json
import os
import threading
import time
import uuid

PAGES = ('all', 'movie', 'series')
SHAPE_CHOICES = ('poster', 'landscape', 'wide', 'square', 'ranked', 'tall', 'tall_ranked', 'collage', 'wide_ranked',
                 'mosaic', 'stack', 'poster_large', 'feature', 'landscape_info',
                 'collection_spotlight', 'collection_compact', 'collection_ranking', 'circle')


# v5.10.448: the gallery shows the styles by kind, each card with its kind's
# tag in its colour: cards, banners, numbers, collections
STYLE_GROUPS = (
    ('cards', 'بطاقات', 'FF6E8BFF'),
    ('banners', 'بانر', 'FFE8A33D'),
    ('numbers', 'أرقام', 'FFE50914'),
    ('collections', 'كوليكشن', 'FF35BFA8'),
)


def style_choices(tr, media_path, only=None):
    """The row styles for the gallery (v5.10.175): (key, name, line, preview,
    group key).

    v5.10.286: each line fits two lines under its tile; the previews are
    rowpreview_<key>.jpg, 896x504 miniatures of the Home row in that style
    (a new name so Kodi's texture cache never shows the old drawings).
    v5.10.448: in the order of their kinds (STYLE_GROUPS)."""
    rows = (('', 'تلقائي', 'كل صف بالشكل المناسب لمحتواه', ''),
            ('poster', 'بوستر', 'البوستر الطولي المعتاد', 'cards'),
            ('poster_large', 'بوستر كبير مع التفاصيل', 'بوستر أكبر بالثلث، والاسم والسنة تحته', 'cards'),
            ('tall', 'بطاقات الوضع', 'بطاقات طويلة، المحددة تكبر وتتفتح بالعرض', 'cards'),
            ('landscape', 'بانر', 'بطاقات عريضة أصغر، يبين صفوف أكثر', 'cards'),
            ('landscape_info', 'بطاقات عريضة مع التفاصيل', 'بطاقات عريضة والاسم والمعلومات تحتها', 'cards'),
            ('square', 'مربع', 'بطاقات مربعة، للقنوات والمجموعات', 'cards'),
            # v5.10.429 (skin 3.28.159): the logo, or the picture, in a circle
            ('circle', 'دوائر الشعارات', 'للمنصات والشبكات والممثلين، الشعار أو الصورة في دائرة', 'cards'),
            ('wide', 'بانر كبير مع الشعار', 'خلفية كبيرة والشعار وكم باقي، مثل Apple TV', 'banners'),
            ('feature', 'بانر سينمائي ضخم', 'صورة عريضة ضخمة مع الشعار وتقدم المشاهدة', 'banners'),
            ('ranked', 'توب 10 بالأرقام', 'بوسترات مرقمة، الرقم جنب البوستر', 'numbers'),
            ('tall_ranked', 'بطاقات الوضع بالأرقام', 'مثل توب 10 في Netflix، الرقم جنب كل بطاقة', 'numbers'),
            ('wide_ranked', 'بانر كبير بالأرقام', 'بانر بالشعار والرقم جنبه، للرائج', 'numbers'),
            ('collection_spotlight', 'مجموعة مع واجهة بارزة', 'صورة بارزة وثلاث معاينات من الكتالوج', 'collections'),
            ('collection_compact', 'مجموعة ببوسترات جانبية', 'رزمة بوسترات بجانب اسم المجموعة أو شعارها', 'collections'),
            ('collection_ranking', 'مجموعة بأول ثلاثة أعمال', 'أول ثلاثة أعمال بالترتيب مع صورها', 'collections'),
            ('collage', 'كوليكشن جدار', 'الكتالوج كله ببانر واحد، بوستراته جدار مائل', 'collections'),
            ('mosaic', 'كوليكشن موزاييك', 'بانر فيه البوسترات مربعات مرتبة مثل ألبوم الصور', 'collections'),
            ('stack', 'كوليكشن مكدّس', 'بانر فيه البوسترات فوق بعض مثل رزمة صور', 'collections'))
    return [(key, tr(name), tr(line), media_path('rowpreview_%s.jpg' % (key or 'auto')), group)
            for key, name, line, group in rows if only is None or key in only]


def suggested_styles(title, collection=False, progress=False):
    """The styles that suit a row, by its name and kind (v5.10.448): a Top 10
    or trending row its numbered styles, a row of services or networks its
    circles and banners, a progress row its big banners."""
    import re
    if progress:
        return ('wide', 'feature')
    if collection:
        return ('circle', 'landscape', 'collection_spotlight')
    name = str(title or '')
    if re.search(r'\btop\s*(?:10|20|25|ten)\b|trend|popular|رائج|الأكثر|توب|أفضل', name, re.I):
        return ('ranked', 'tall_ranked', 'wide_ranked')
    if re.search(r'network|studio|service|provider|شبك|استوديو|منص', name, re.I):
        return ('circle',)
    return ()


COLLECTION_STYLES = ('collage', 'mosaic', 'stack', 'collection_spotlight', 'collection_compact', 'collection_ranking')
# v5.10.189: a progress row (Continue Watching, Next Up, a server's or an
# add-on's own) keeps its cards: drawn as one collection banner it lost its
# resume cards (skin.dexhub drew them one long card each)
PROGRESS_STYLES = tuple(k for k in ('',) + SHAPE_CHOICES if k not in COLLECTION_STYLES)


def is_progress_row(row):
    meta = getattr(row, 'meta', None) or {}
    return getattr(row, 'kind', '') == 'continue' or bool(meta.get('progress')) or \
        getattr(row, 'key', '') in ('continue', 'nextup')


def style_choices_for(shelf=False, progress=False):
    """The styles the gallery offers for a row (None: all)."""
    if shelf:
        return COLLECTION_STYLES
    if progress:
        return PROGRESS_STYLES
    return None


def pick_style(tr, media_path, current, title, only=None, suggested=()):
    """The gallery; the chosen key ('' automatic), or None when Back.
    ``only``: the keys offered (a collection row: its three looks);
    ``suggested``: the keys tagged as suiting the row."""
    from .options import choose
    choices = style_choices(tr, media_path, only)
    groups = dict((key, (tr(name), tint)) for key, name, tint in STYLE_GROUPS)
    options = [(name, 'current' if key == current else '', preview, line,
                groups.get(group, ('', ''))[0], groups.get(group, ('', ''))[1],
                tr('مقترح') if key in suggested else '')
               for key, name, line, preview, group in choices]
    keys = [c[0] for c in choices]
    pick = choose(tr('شكل الصف'), options, preselect=keys.index(current) if current in keys else 0,
                  subtitle=title, layout='gallery')
    if pick < 0:
        return None
    return keys[pick]


def page_key(media):
    media = str(media or 'all')
    return media if media in PAGES else 'all'


# v5.10.238: skin.dexhub's Library and Servers tabs keep an order of their own
# too (arranged from the phone); the Home editor on the TV still has the three
TAB_PAGES = PAGES + ('library', 'servers')


def store_key(media):
    media = str(media or 'all')
    return media if media in TAB_PAGES else 'all'


def retired_af3(spec):
    """Recognize only the removed importer, never a user's label or AF3 skin."""
    from urllib.parse import parse_qsl, urlsplit
    if not isinstance(spec, dict):
        return False
    meta = spec.get('meta') or {}
    if str(spec.get('key') or '').startswith('af3:'):
        return True
    if any('af3-community:' in str(spec.get(k) or '') for k in ('community', 'set_id', 'group_id')):
        return True
    if any(str(ref.get('group_id') or '').startswith('af3-community:')
           for ref in spec.get('folders') or [] if isinstance(ref, dict)):
        return True
    for params in (spec.get('params'), meta.get('params')):
        params = dict(params or {})
        for _ in range(8):
            if params.get('action') != 'skin_list':
                break
            params = dict(parse_qsl(params.get('r') or ''))
        if params.get('action') in ('skin_af3_catalog', 'skin_af3_import'):
            return True
    return any(urlsplit(str(tile.get('path') or '')).netloc == 'plugin.video.dexhub'
               and dict(parse_qsl(urlsplit(str(tile.get('path') or '')).query)).get('action') == 'skin_af3_catalog'
               for tile in spec.get('tiles') or [] if isinstance(tile, dict))


def custom_key(spec):
    return 'my:%s' % (spec or {}).get('id', '')


def new_custom(spec):
    spec = dict(spec or {})
    spec['id'] = uuid.uuid4().hex[:10]
    spec['added'] = int(time.time())
    return spec


def _hidden(row, hidden):
    value = hidden.get(row.key)
    if value is None:
        return bool((row.meta or {}).get('default_hidden'))
    return bool(value)


def arrange(rows, page):
    """[(row, hidden)] for every row, in the saved order."""
    rows = list(rows or [])
    order = [k for k in (page or {}).get('order') or [] if k]
    hidden = (page or {}).get('hidden') or {}
    if not order:
        return [(row, _hidden(row, hidden)) for row in rows]
    position = {}
    for index, key in enumerate(order):
        position.setdefault(key, index)
    out = sorted([r for r in rows if r.key in position], key=lambda r: position[r.key])
    placed = set(r.key for r in out)
    for index, row in enumerate(rows):
        if row.key in placed:
            continue
        # after the nearest earlier row (in the Home's own order) already placed
        at = 0
        for previous in reversed(rows[:index]):
            if previous.key in placed:
                at = next(i for i, r in enumerate(out) if r.key == previous.key) + 1
                break
        out.insert(at, row)
        placed.add(row.key)
    return [(row, _hidden(row, hidden)) for row in out]


def _skin_follows():
    """skin.dexhub publishes its rows again after a change (v5.10.109)."""
    try:
        import xbmc
        if xbmc.getSkinDir() == 'skin.dexhub':
            import xbmcgui
            xbmcgui.Window(10000).setProperty('dhs.republish', '%.3f layout' % time.time())
    except Exception:
        pass


# v5.10.448: Dex Hub's own collection rows open as big banners (a user's
# pick for a row still wins)
DEFAULT_SHAPES = {'col:collection-DEXGENRES': 'wide', 'col:collection-DEXMOODS': 'wide',
                  'col:collection-DEXKWAVE': 'wide', 'col:collection-DEXSHELF': 'wide'}


class Layout(object):
    def __init__(self, profile):
        self._path = _scope.ScopedPath('homeui/layout.json')
        self._lock = threading.RLock()
        self._data = None
        self._mtime = None

    # ------------------------------------------------------------ storage
    def _load(self):
        try:
            mtime = (_scope.key(), os.path.getmtime(self._path))
        except OSError:
            mtime = (_scope.key(), None)
        if self._data is not None and mtime == self._mtime:
            return self._data
        data = {}
        if mtime is not None:
            try:
                with open(self._path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle) or {}
            except Exception:
                data = {}
        pages = data.get('pages') if isinstance(data, dict) else None
        shapes = data.get('shapes') if isinstance(data, dict) else None
        known = data.get('known') if isinstance(data, dict) else None
        self._data = {'version': 1, 'pages': pages if isinstance(pages, dict) else {},
                      # v5.10.173: a row's own shape (any page, any tab), by row key
                      'shapes': dict((str(k), str(v)) for k, v in (shapes or {}).items()
                                     if v in SHAPE_CHOICES) if isinstance(shapes, dict) else {}}
        if isinstance(known, dict):
            self._data['known'] = known     # v5.10.236: rows seen per page (Hide new rows)
        self._mtime = mtime
        return self._data

    def _save(self):
        tmp = self._path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
            self._mtime = (_scope.key(), os.path.getmtime(self._path))
            _skin_follows()
            try:
                from .. import cloud_look      # v5.10.366: the order goes to the cloud
                cloud_look.mark()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def page(self, media):
        with self._lock:
            raw = self._load()['pages'].get(store_key(media)) or {}
            return {
                'order': [str(k) for k in (raw.get('order') or []) if k],
                'hidden': dict((str(k), bool(v)) for k, v in (raw.get('hidden') or {}).items()),
                'custom': [dict(c) for c in (raw.get('custom') or [])
                           if isinstance(c, dict) and c.get('id') and not retired_af3(c)],
            }

    def save_page(self, media, order=None, hidden=None, custom=None):
        with self._lock:
            data = self._load()
            page = self.page(media)
            if order is not None:
                page['order'] = [str(k) for k in order if k]
            if hidden is not None:
                page['hidden'] = dict((str(k), bool(v)) for k, v in hidden.items())
            if custom is not None:
                page['custom'] = [dict(c) for c in custom if isinstance(c, dict) and c.get('id')]
            data['pages'][store_key(media)] = page
            return self._save()

    def reset_page(self, media):
        """Back to the Home's own order and visibility; added rows stay (at the end)."""
        with self._lock:
            page = self.page(media)
            return self.save_page(media, order=[], hidden={}, custom=page['custom'])

    def hide(self, media, key, hidden=True):
        with self._lock:
            page = self.page(media)
            page['hidden'][key] = bool(hidden)
            return self.save_page(media, hidden=page['hidden'])

    def hide_new(self, media, keys):
        """v5.10.236 (Hide new rows from add-ons): the add-on rows first seen
        now start hidden (Arrange Home rows shows them). The rows there when
        the option was turned on stay as they were. Returns the keys hidden."""
        keys = [str(k) for k in keys or [] if k]
        with self._lock:
            data = self._load()
            known = data.setdefault('known', {})
            seen = known.get(page_key(media))
            if not isinstance(seen, list):
                known[page_key(media)] = sorted(set(keys))
                self._save_quiet()
                return []
            fresh = [k for k in keys if k not in set(seen)]
            if not fresh:
                return []
            page = self.page(media)
            for key in fresh:
                page['hidden'].setdefault(key, True)
            known[page_key(media)] = sorted(set(seen) | set(fresh))[-2000:]
            data['pages'][page_key(media)] = page
            self._save()
            return fresh

    def forget_known(self):
        """The option turned off: next time it starts from the rows then shown."""
        with self._lock:
            data = self._load()
            if data.pop('known', None) is not None:
                self._save_quiet()

    def _save_quiet(self):
        """Saved without asking the skin to publish again (nothing shown changed)."""
        tmp = self._path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
            self._mtime = (_scope.key(), os.path.getmtime(self._path))
            return True
        except Exception:
            return False

    def shape(self, key):
        """The shape the user picked for a row ('' when the row keeps its own)."""
        with self._lock:
            key = str(key or '')
            return self._load().get('shapes', {}).get(key, '') or DEFAULT_SHAPES.get(key, '')

    def set_shape(self, key, shape=''):
        with self._lock:
            data = self._load()
            shapes = data.setdefault('shapes', {})
            if shape in SHAPE_CHOICES:
                shapes[str(key)] = shape
            else:
                shapes.pop(str(key), None)
            return self._save()


    def stamp(self):
        """Changes whenever the saved layout changes (any page)."""
        with self._lock:
            self._load()
            return self._mtime

    # ------------------------------------------------------------ applying
    def arrange(self, rows, media):
        out = arrange(rows, self.page(media))
        self.shape_rows([row for row, _ in out])
        return out

    def shape_rows(self, rows):
        """Give each row the shape the user picked for it (v5.10.173)."""
        with self._lock:
            shapes = dict(DEFAULT_SHAPES, **(self._load().get('shapes') or {}))
        for row in rows or []:
            pick = shapes.get(getattr(row, 'key', ''))
            if pick in COLLECTION_STYLES and is_progress_row(row):
                pick = ''       # v5.10.189: a progress row keeps its own cards
            if pick:
                row.shape = pick
                if isinstance(row.meta, dict):
                    row.meta['auto_shape'] = False
        return rows

    def apply(self, rows, media):
        """The rows to show, in the user's order."""
        return [row for row, hidden in self.arrange(rows, media) if not hidden]


def continue_split(split):
    """v5.10.210: Continue Watching on the Home as one row, or as two rows
    (films, series) in its place (Menu, Following and library). The rows
    already exist (window._candidate_rows); this shows one shape and hides
    the other on the All page, the way the layout editor would."""
    import xbmcaddon
    import xbmcvfs
    profile = os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')),
                           'homeui')
    layout = Layout(profile)
    with layout._lock:
        page = layout.page('all')
        hidden = dict(page['hidden'])
        hidden['continue'] = bool(split)
        hidden['continue_movies'] = not split
        hidden['continue_shows'] = not split
        order = list(page['order'])
        if order:
            order = [k for k in order if k not in ('continue_movies', 'continue_shows')]
            at = order.index('continue') + 1 if 'continue' in order else 0
            order[at:at] = ['continue_movies', 'continue_shows']
        return layout.save_page('all', order=order if page['order'] else None, hidden=hidden)


def continue_is_split():
    import xbmcaddon
    import xbmcvfs
    profile = os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile')),
                           'homeui')
    hidden = Layout(profile).page('all')['hidden']
    return hidden.get('continue') is True and hidden.get('continue_movies') is False

