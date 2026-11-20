# -*- coding: utf-8 -*-
"""An actor's (or director's) page (v5.10.595), after Silo's app (the user,
21 Nov 2026): their photo, name, details and story over a tilted wall of
their posters, then Known For (wide pictures), Movies, TV Shows, Directed and
a Timeline of every part, newest first. The page takes the colour of their
best-known title, as the search and the Home do.

WindowXMLDialog on dexhub_person*.xml (tools/make_person_window.py). Opened
from the title page's cast row (skinui/title.py, action home_person) and from
a person in the search. One TMDb request (/person/{id} with its combined
credits), kept three days; the story in English when the interface language
has none. A credit opens its title page over this one; Back is this page.
"""
import threading

import xbmc
import xbmcgui

KNOWN, MOVIES, TV, DIRECTED, TIMELINE = 301, 302, 303, 304, 305
ROWS = (KNOWN, MOVIES, TV, DIRECTED, TIMELINE)
A_BACK = (9, 10, 92, 216, 247, 257, 275, 61467, 61448)
TTL = 3 * 24 * 3600
SKIP_GENRES = {10763, 10767}         # news and talk shows: a guest's every visit
PAGE_TONE = 'FF0B0C12'
MONTHS_AR = ('يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر', 'أكتوبر', 'نوفمبر', 'ديسمبر')
MONTHS_EN = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def _log(text, level=xbmc.LOGINFO):
    xbmc.log('[DexHub] person page: %s' % text, level)


def _img(path, size):
    from .. import tmdb_direct as T
    if not path:
        return ''
    base = T.IMAGE_BASE_POSTER.rsplit('/', 1)[0]
    return '%s/%s%s' % (base, size, path)


def person_data(person_id):
    """TMDb's person with their combined credits, cached; {} when unknown."""
    from .. import tmdb_direct as T
    lang = (T._card_language() or 'en-US')
    key = 'dh-person-v1:%s:%s' % (person_id, lang)
    data = T._cache_get(key)
    if isinstance(data, dict) and data:
        return data
    data = T._request('/person/%s' % person_id, {'append_to_response': 'combined_credits', 'language': lang},
                      timeout=8, max_attempts=2) or {}
    if data and not (data.get('biography') or '').strip() and not lang.lower().startswith('en'):
        english = T._request('/person/%s' % person_id, {'language': 'en-US'}, timeout=6, max_attempts=1) or {}
        data['biography'] = english.get('biography') or ''
    if data:
        T._cache_set(key, data, TTL)
    return data


def _date(entry):
    return str(entry.get('release_date') or entry.get('first_air_date') or '')


def _credit(entry, role):
    media = 'series' if entry.get('media_type') == 'tv' else 'movie'
    date = _date(entry)
    return {'tmdb': str(entry.get('id') or ''), 'media': media, 'title': entry.get('title') or entry.get('name') or '',
            'date': date, 'year': date[:4], 'month': int(date[5:7]) if len(date) >= 7 and date[5:7].isdigit() else 0,
            'poster': _img(entry.get('poster_path'), 'w342'), 'fanart': _img(entry.get('backdrop_path'), 'w780'),
            'votes': int(entry.get('vote_count') or 0), 'rating': float(entry.get('vote_average') or 0),
            'popularity': float(entry.get('popularity') or 0), 'role': role,
            'genres': set(entry.get('genre_ids') or [])}


def shape(data):
    """The page's sections from TMDb's person: {known, movie, tv, directed,
    timeline, wall, credits}."""
    credits = data.get('combined_credits') or {}
    cast = [_credit(e, ('character', e.get('character') or '')) for e in credits.get('cast') or [] if isinstance(e, dict)]
    crew = [_credit(e, ('job', e.get('job') or '')) for e in credits.get('crew') or [] if isinstance(e, dict)]
    cast = [c for c in cast if c['title'] and not (c['genres'] & SKIP_GENRES)]
    directed = [c for c in crew if c['title'] and c['role'][1] == 'Director']

    def unique(rows):
        seen, out = set(), []
        for row in rows:
            key = (row['media'], row['tmdb'])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    department = data.get('known_for_department') or ''
    pool = unique((directed + cast) if department == 'Directing' else (cast + directed))
    known = sorted([c for c in pool if c['fanart']], key=lambda c: (-c['votes'], -c['popularity']))[:15]
    def newest(rows):
        # newest first; a title with no poster yet (most often one not out)
        # goes to the row's end instead of opening it with an empty card
        rows = sorted(rows, key=lambda c: c['date'] or '0000', reverse=True)
        return [c for c in rows if c['poster']] + [c for c in rows if not c['poster']]
    movies = newest(unique([c for c in cast if c['media'] == 'movie']))
    shows = newest(unique([c for c in cast if c['media'] == 'series']))
    directed = newest(unique(directed))
    # the timeline keeps its order: every part where it falls
    timeline = sorted(unique([c for c in cast + directed if c['date']]), key=lambda c: c['date'], reverse=True)[:60]
    wall = [c['poster'] for c in sorted(pool, key=lambda c: -c['popularity']) if c['poster']][:12]
    every = {(c['media'], c['tmdb']) for c in cast} | {(c['media'], c['tmdb']) for c in crew}
    return {'known': known, 'movie': movies, 'tv': shows, 'directed': directed, 'timeline': timeline,
            'wall': wall, 'credits': len(every)}


class PersonWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(PersonWindow, self).__init__(*args)
        self.app = kwargs.get('app')
        self.person_id = str(kwargs.get('person_id') or '')
        self.name = kwargs.get('name') or ''
        self.rows = {}
        self.next = None
        self._closing = False
        self._inited = False

    def tr(self, text):
        return self.app.tr(text) if self.app is not None else text

    def _arabic(self):
        try:
            return xbmc.getInfoLabel('System.Language').lower().startswith('arab')
        except Exception:
            return False

    def onInit(self):
        if self._inited:
            return
        self._inited = True
        self.setProperty('tone', PAGE_TONE)
        self.setProperty('name', self.name)
        words = [w for w in self.name.split() if w]
        self.setProperty('initials', ''.join(w[0] for w in words[:2]).upper())
        self.setProperty('busy', self.tr('جاري التحميل…'))
        for key, title in (('known', 'معروف بـ'), ('movie', 'أفلام'), ('tv', 'مسلسلات'),
                           ('directed', 'أعمال من إخراجه'), ('timeline', 'الخط الزمني')):
            self.setProperty('row.%s' % key, self.tr(title))
        threading.Thread(target=self._load, name='DexHubPersonPage', daemon=True).start()

    # ---------------------------------------------------------------- data
    def _load(self):
        try:
            data = person_data(self.person_id) if self.person_id.isdigit() else {}
        except Exception as exc:
            _log('read %s: %s' % (self.person_id, exc), xbmc.LOGWARNING)
            data = {}
        if self._closing:
            return
        if not data:
            self.setProperty('busy', self.tr('ما لقينا صفحة هذا الشخص'))
            return
        page = shape(data)
        self.setProperty('name', data.get('name') or self.name)
        self.setProperty('photo', _img(data.get('profile_path'), 'w500'))
        self.setProperty('meta', self._meta(data))
        self.setProperty('credits', self.tr('%d مشاركة') % page['credits'] if page['credits'] else '')
        self.setProperty('bio', (data.get('biography') or '').strip())
        self.setProperty('busy', '')
        for index, poster in enumerate(page['wall']):
            self.setProperty('wall.%d' % index, poster)
        for cid, key in ((KNOWN, 'known'), (MOVIES, 'movie'), (TV, 'tv'), (DIRECTED, 'directed'), (TIMELINE, 'timeline')):
            self._fill(cid, key, page[key])
        self._focus_first()
        known = page['known'] or page['movie'] or page['tv']
        if known:
            self._tint(known[0].get('fanart') or known[0].get('poster'))

    def _meta(self, data):
        parts = []
        department = data.get('known_for_department') or ''
        names = {'Acting': 'تمثيل', 'Directing': 'مخرج', 'Writing': 'كتابة', 'Production': 'إنتاج'}
        if department:
            parts.append(self.tr(names.get(department, department)))
        born = str(data.get('birthday') or '')
        if born[:4].isdigit():
            parts.append(self.tr('وُلد %s') % born[:4])
        died = str(data.get('deathday') or '')
        if died[:4].isdigit():
            parts.append(self.tr('توفي %s') % died[:4])
        place = str(data.get('place_of_birth') or '').strip()
        if place:
            parts.append(place)
        return u'  ·  '.join(parts)

    def _fill(self, cid, key, rows):
        try:
            ctrl = self.getControl(cid)
        except Exception:
            return
        items = []
        for row in rows:
            li = xbmcgui.ListItem(row['title'])
            kind = self.tr('فيلم') if row['media'] == 'movie' else self.tr('مسلسل')
            if key == 'known':
                bits = [row['year'], kind]
                if row['rating'] and row['votes'] >= 20:
                    bits.append(u'★ %.1f' % row['rating'])
                li.setLabel2(u'  ·  '.join(b for b in bits if b))
            else:
                li.setLabel2(row['year'])
            li.setArt({'poster': row['poster'], 'thumb': row['poster'], 'fanart': row['fanart']})
            li.setProperty('year', row['year'])
            if row['month']:
                months = MONTHS_AR if self._arabic() else MONTHS_EN
                li.setProperty('month', months[row['month'] - 1])
            li.setProperty('kind', kind)
            what, part = row['role']
            if what == 'character' and part:
                li.setProperty('role', self.tr('بدور %s') % part)
            elif what == 'job':
                li.setProperty('role', self.tr('مخرج') if part == 'Director' else part)
            items.append(li)
        self.rows[cid] = list(rows)
        self.setProperty('row.%s.n' % key, str(len(rows)) if rows else '')
        ctrl.reset()
        if items:
            ctrl.addItems(items)

    def _focus_first(self):
        for cid in ROWS:
            if self.rows.get(cid):
                self.setFocusId(cid)
                return

    def _tint(self, url):
        if not url:
            return

        def run():
            try:
                from .. import art_colour
                found = art_colour.colour(url)
            except Exception:
                found = None
            if found and not self._closing:
                self.setProperty('tone', found.get('tone') or PAGE_TONE)
                self.setProperty('glow', found.get('glow') or '')

        threading.Thread(target=run, name='DexHubPersonTint', daemon=True).start()

    # -------------------------------------------------------------- events
    def onClick(self, cid):
        rows = self.rows.get(cid) or []
        try:
            pos = self.getControl(cid).getSelectedPosition()
        except Exception:
            pos = -1
        if not 0 <= pos < len(rows):
            return
        row = rows[pos]
        if not row.get('tmdb'):
            return
        q = {'m': row['media'], 'title': row['title'], 'tmdb': row['tmdb'], 'imdb': ''}
        try:
            from ..skinui.items import title_over_window
            if title_over_window(self, q):
                return
        except Exception as exc:
            _log('title page: %s' % exc, xbmc.LOGWARNING)
        self.next = {'kind': 'work', 'media_type': row['media'], 'tmdb_id': row['tmdb'], 'title': row['title'],
                     'poster': row['poster'], 'fanart': row['fanart'], 'year': row['year']}
        self.close()

    def onAction(self, action):
        if action.getId() in A_BACK:
            self._closing = True
            self.close()


def open_person(app, params):
    """The page; a credit the skin's title page cannot show opens the
    add-on's own details after it closes."""
    from . import ui_size
    person_id = str(params.get('person_id') or params.get('id') or '')
    if not person_id.isdigit():
        return
    window = PersonWindow(ui_size.xml('dexhub_person.xml'), app.addon_path, 'Default', '1080i',
                          app=app, person_id=person_id, name=params.get('name') or '')
    app._show(window)
    tile = window.next
    del window
    if tile:
        try:
            app.open_details(tile)
        except Exception as exc:
            _log('details: %s' % exc, xbmc.LOGWARNING)
