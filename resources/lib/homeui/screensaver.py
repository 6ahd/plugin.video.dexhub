# -*- coding: utf-8 -*-
"""The Dex Hub screensaver: "Cinema, at rest" (v5.10.176).

Dex Hub is a Kodi screensaver too (addon.xml: xbmc.ui.screensaver,
library screensaver.py). The page is a cinema at rest: a backdrop that pans
slowly and changes, a title from TMDb with its logo, Arabic name, line and
story on the left, and on the right the clock, a week of what is coming
(the days that have something marked), the coming titles with their
posters and a countdown to the one in front.

What it shows comes from TMDb, as these TMDb pages list them: airing today,
new on Netflix, new on the streaming services (series and films), top rated
series, and what is coming (films coming out, the next episodes of the
series on the air this week, and the new episodes of the user's own shows
from Floppy or Trakt). It is kept for six hours in the Home's profile folder
(ss_cinema.json) so the screensaver opens at once; the Home's own saved
titles fill in until TMDb has answered.

Your List and News (v5.10.357) are drawn by ss_windows.py.

As a screensaver any key ends it. Opened from the Home's menu (Screensaver,
Preview) it can be browsed: Left and Right go through the days, Up and Down
through the titles, OK shows the next title, Back leaves.

Layout contract with resources/skins/Default/1080i/dexhub_screensaver.xml:
  ss.slot ('a'/'b'), ss.<slot>.fanart / logo / title / meta / plot / row /
  zoom / kb / long; ss.brand, ss.empty; ss.tagline, ss.scene, ss.mark;
  ss.month, ss.heading, ss.section, ss.count, ss.count.label, ss.count.title,
  ss.cal ('1' when the rail has entries); v5.10.357: no key hints (ss.browse,
  ss.next, ss.resume are gone);
  list 310 the week (props weekday, day, has, sel); list 300 the titles
  (label, label2 details, props when, rel, active; art poster).
"""
import datetime
from .. import profile_scope as _scope
import json
import os
import random
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.dexhub'
SLIDE = 14.0            # seconds a title stays
FADE = 1.8              # the crossfade (the skin's own fade time, plus a margin)
MAX_SLIDES = 80
CINEMA_TTL = 6 * 3600
WEEK_LIST, RELEASE_LIST = 310, 300
# v5.10.334: the Up next style's lists (what you are watching, your week)
CONTINUE_LIST, TIMELINE_LIST = 330, 340
DOTS = 5
_TYPES = {'movie': 'فيلم', 'series': 'مسلسل', 'episode': 'حلقة'}
A_LEFT, A_RIGHT, A_UP, A_DOWN, A_SELECT = 1, 2, 3, 4, 7
A_BACK = (9, 10, 92, 216, 247, 257, 275, 61448, 61467)
A_SKIP = (11, 77, 14, 20, 21)      # Info, Fast forward, Skip next, Page down, Next: the next title
_MONTHS_AR = ('يناير', 'فبراير', 'مارس', 'أبريل', 'مايو', 'يونيو', 'يوليو', 'أغسطس', 'سبتمبر',
              'أكتوبر', 'نوفمبر', 'ديسمبر')
_MONTHS_EN = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
              'October', 'November', 'December')
_WEEK_AR = ('إثنين', 'ثلاثاء', 'أربعاء', 'خميس', 'جمعة', 'سبت', 'أحد')
_WEEK_EN = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_DAYS_AR = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
_DAYS_EN = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def _addon():
    return xbmcaddon.Addon(ADDON_ID)


def _profile():
    return _scope.path('homeui')


def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def _english():
    try:
        from ..i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def _story(text):
    """Three lines of the story at most (the facts block of a server copy left out)."""
    try:
        from .rows import strip_facts, split_server_facts
        text = strip_facts(split_server_facts(text)[1])
    except Exception:
        text = str(text or '')
    text = ' '.join(str(text or '').split())
    return text if len(text) <= 260 else text[:257].rsplit(' ', 1)[0] + '…'


def _log(msg):
    xbmc.log('[DexHub] screensaver: %s' % msg, xbmc.LOGINFO)


# ------------------------------------------------------------- TMDb
def _region():
    try:
        value = (_addon().getSetting('screensaver_region') or '').strip().upper()
    except Exception:
        value = ''
    return value if len(value) == 2 else ('US' if _english() else 'SA')


def _tmdb(path, params=None, ttl=CINEMA_TTL):
    """A TMDb list (cached in Dex Hub's TMDb cache)."""
    from .. import tmdb_direct as T
    if not T._api_key():
        return {}
    query = dict(params or {})
    key = 'cinema:%s?%s' % (path, '&'.join('%s=%s' % kv for kv in sorted(query.items())))
    cached = T._cache_get(key)
    if cached:
        return cached
    try:
        data = T._request(path, query, timeout=8) or {}
    except Exception as exc:
        _log('%s failed (%s)' % (path, exc.__class__.__name__))
        return {}
    if data:
        T._cache_set(key, data, ttl)
    return data


def _image(path, kind):
    from .. import tmdb_direct as T
    return T._image_url(path or '', kind=kind) if path else ''


SECTION_KEYS = ('airing', 'netflix', 'streaming_tv', 'streaming_movie', 'top_tv', 'trending_tv', 'trending_movie',
                'now_playing', 'popular_movie', 'top_movie')
DEFAULT_SECTIONS = ('airing', 'netflix', 'streaming_tv', 'streaming_movie', 'top_tv', 'trending_tv', 'trending_movie')
MEDIA_CHOICES = ('both', 'movie', 'series')


def chosen_sections():
    """The TMDb lists the user keeps (settings: screensaver_sources)."""
    try:
        raw = (_addon().getSetting('screensaver_sources') or '').strip()
    except Exception:
        raw = ''
    if not raw:
        return list(DEFAULT_SECTIONS)
    if raw == 'none':
        return []           # v5.10.335: no TMDb list at all
    keys = [k for k in raw.split(',') if k in SECTION_KEYS]
    return keys or list(DEFAULT_SECTIONS)


def set_tmdb(on):
    """v5.10.335: TMDb's lists in the screensaver, on or off (the sources
    page). Off: none of them; on: the lists kept, else the defaults, and
    the screensaver no longer limited to your own sources."""
    from .. import watch_sources as W
    cfg = W.config()
    if on:
        if not chosen_sections():
            _addon().setSetting('screensaver_sources', ','.join(DEFAULT_SECTIONS))
        cfg['personal_only'] = False
    else:
        _addon().setSetting('screensaver_sources', 'none')
    W._write('watch_sources.json', cfg)
    _forget()


def chosen_media():
    """'both', 'movie' or 'series' (settings: screensaver_media)."""
    try:
        value = (_addon().getSetting('screensaver_media') or '').strip().lower()
    except Exception:
        value = ''
    return value if value in MEDIA_CHOICES else 'both'


def _config():
    from .. import watch_sources as W
    return '%s|%s|%s|%s|%s' % (','.join(chosen_sections()), chosen_media(), _region(), W._fingerprint(W.config()), str(W.config().get('personal_only')) + ':' + str(W.snapshot().get('t') or 0))


def _all_sections():
    """(key, name, path, params, media) of the TMDb lists the screensaver shows."""
    today = datetime.date.today()
    lang = 'en-US' if _english() else 'ar-SA'
    region = _region()
    recent = {'air_date.gte': (today - datetime.timedelta(days=30)).isoformat(),
              'air_date.lte': today.isoformat()}
    base = {'language': lang}
    return (
        ('airing', 'يُعرض اليوم', '/tv/airing_today', dict(base), 'series'),
        ('netflix', 'جديد على Netflix', '/discover/tv',
         dict(base, with_watch_providers='8', watch_region=region, sort_by='popularity.desc', **recent), 'series'),
        ('streaming_tv', 'جديد على المنصات', '/discover/tv',
         dict(base, with_watch_monetization_types='flatrate', watch_region=region,
              sort_by='popularity.desc', **recent), 'series'),
        ('streaming_movie', 'أفلام جديدة على المنصات', '/discover/movie',
         dict(base, with_watch_monetization_types='flatrate', watch_region=region, sort_by='popularity.desc',
              with_release_type='4|5', **{'release_date.gte': (today - datetime.timedelta(days=60)).isoformat(),
                                         'release_date.lte': today.isoformat()}), 'movie'),
        ('top_tv', 'الأعلى تقييماً', '/tv/top_rated', dict(base), 'series'),
        ('trending_tv', 'مسلسلات رائجة هذا الأسبوع', '/trending/tv/week', dict(base), 'series'),
        ('trending_movie', 'أفلام رائجة هذا الأسبوع', '/trending/movie/week', dict(base), 'movie'),
        ('now_playing', 'في السينما الآن', '/movie/now_playing', dict(base, region=region), 'movie'),
        ('popular_movie', 'أفلام شائعة', '/movie/popular', dict(base, region=region), 'movie'),
        ('top_movie', 'أفلام الأعلى تقييماً', '/movie/top_rated', dict(base), 'movie'),
    )


def _sections():
    """The lists the user chose, for the kind they chose (films, series or both)."""
    keep, media = chosen_sections(), chosen_media()
    return [s for s in _all_sections() if s[0] in keep and (media == 'both' or s[4] == media)]


def _year(row):
    raw = str(row.get('first_air_date') or row.get('release_date') or '')
    return raw[:4] if raw[:4].isdigit() else ''


def _features():
    """Titles for the left side: five of each TMDb list, with a backdrop."""
    from .. import watch_sources as W
    personal = [r for r in W.features() if chosen_media() == 'both' or r['media'] == chosen_media()]
    if W.config().get('personal_only'):
        return personal
    out, seen = list(personal), {(r['media'], r.get('tmdb_id')) for r in personal}
    for key, name, path, params, media in _sections():
        data = _tmdb(path, params)
        picked = 0
        for row in (data.get('results') or []):
            if picked >= 5:
                break
            tid = str(row.get('id') or '')
            if not tid or not row.get('backdrop_path') or (media, tid) in seen:
                continue
            seen.add((media, tid))
            picked += 1
            meta = [_tr(_TYPES[media]), _year(row)]
            vote = row.get('vote_average') or 0
            if vote:
                meta.append(u'★ %.1f' % float(vote))
            out.append({'tmdb_id': tid, 'media': media, 'section': _tr(name),
                        'chip': _tr(name), 'kind': 'tmdb', 'year': _year(row),
                        'rating': ('%.1f' % float(vote)) if vote else '',
                        'fanart': _image(row.get('backdrop_path'), 'backdrop'),
                        'title': str(row.get('name') or row.get('title') or ''),
                        'meta': '   ·   '.join(m for m in meta if m),
                        'plot': _story(row.get('overview')), 'logo': ''})
    # v5.10.334: the window orders them (shuffled, or what you watch first)
    return out


def _calendar():
    """What is coming, from today: [{date, title, detail, poster, kind}]."""
    today = datetime.date.today()
    end = today + datetime.timedelta(days=7)
    lang = 'en-US' if _english() else 'ar-SA'
    entries, seen = [], set()

    media = chosen_media()

    def add(day, title, detail, poster, kind, tmdb_id=''):
        if not title or day < today or day > end:
            return
        if (media == 'movie' and kind != 'movie') or (media == 'series' and kind == 'movie'):
            return
        key = (day.isoformat(), title)
        if key in seen:
            return
        seen.add(key)
        entries.append({'date': day.isoformat(), 'title': title, 'detail': detail, 'poster': poster, 'kind': kind,
                        'tmdb_id': str(tmdb_id or ''), 'media': 'movie' if kind == 'movie' else 'series'})
    from .. import watch_sources as W
    for ev in W.calendar_events():
        try:
            add(datetime.date.fromisoformat(ev['date']), ev['title'], ev['detail'], ev['poster'], ev['kind'], ev['tmdb_id'])
        except (ValueError, KeyError):
            continue
    if W.config().get('personal_only'):
        entries.sort(key=lambda e: e['date'])
        return entries[:30]
    # the user's own shows first (Floppy or Trakt, as My Library's calendar)
    try:
        from ..routes import library
        for ev in library._calendar_events():
            add(ev['date'], ev.get('show') or '', 'S%02dE%02d' % (ev.get('season') or 0, ev.get('episode') or 0),
                ev.get('poster') or '', 'mine', ev.get('tmdb') or '')
    except Exception:
        pass
    tmdb_on = bool(chosen_sections())     # v5.10.335: TMDb's lists off: none of its calendar
    # films coming out
    for row in [] if media == 'series' or not tmdb_on else (_tmdb('/movie/upcoming', {'language': lang, 'region': _region()}).get('results') or [])[:20]:
        try:
            day = datetime.date.fromisoformat(str(row.get('release_date') or '')[:10])
        except ValueError:
            continue
        add(day, str(row.get('title') or ''), _tr('فيلم'), _image(row.get('poster_path'), 'poster'), 'movie',
            row.get('id'))
    # the next episodes of the series on the air this week (the most popular few)
    shows = {} if media == 'movie' or not tmdb_on else _tmdb('/discover/tv', {'language': lang, 'sort_by': 'popularity.desc',
                                   'air_date.gte': today.isoformat(), 'air_date.lte': end.isoformat()})
    for row in [] if media == 'movie' else (shows.get('results') or [])[:8]:
        detail = _tmdb('/tv/%s' % row.get('id'), {'language': lang}, ttl=3 * 3600)
        nxt = (detail or {}).get('next_episode_to_air') or {}
        try:
            day = datetime.date.fromisoformat(str(nxt.get('air_date') or '')[:10])
        except ValueError:
            continue
        add(day, str(row.get('name') or ''), 'S%02dE%02d' % (nxt.get('season_number') or 0, nxt.get('episode_number') or 0),
            _image(row.get('poster_path'), 'poster'), 'episode', row.get('id'))
    entries.sort(key=lambda e: (e['date'], 0 if e['kind'] == 'mine' else 1))
    return entries[:30]


@_scope.bound
def cinema(refresh=False):
    """(features, calendar, fresh) kept for six hours."""
    path = os.path.join(_profile(), 'ss_cinema.json')
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            cached = json.load(handle) or {}
    except Exception:
        cached = {}
    same = cached.get('lang') == _english() and cached.get('cfg', _config()) == _config()
    fresh = time.time() - float(cached.get('t') or 0) < CINEMA_TTL and same
    if not refresh and (fresh or (same and cached.get('features'))):
        return list(cached.get('features') or []), list(cached.get('calendar') or []), fresh
    try:
        features = _features()
    except Exception as exc:
        _log('features failed (%s)' % exc)
        features = []
    try:
        calendar = _calendar()
    except Exception as exc:
        _log('calendar failed (%s)' % exc)
        calendar = []
    if features or calendar:
        try:
            os.makedirs(_profile(), exist_ok=True)
            with open(path + '.tmp', 'w', encoding='utf-8') as handle:
                json.dump({'t': time.time(), 'lang': _english(), 'cfg': _config(), 'features': features,
                           'calendar': calendar},
                          handle, ensure_ascii=False)
            os.replace(path + '.tmp', path)
        except Exception:
            pass
    return features, calendar, True


def _logo_for(feature):
    """The title's logo (TMDb, the user's language first), once."""
    if feature.get('logo') or feature.get('_logo_done'):
        return feature.get('logo') or ''
    feature['_logo_done'] = True
    try:
        from .. import tmdb_direct as T
        found = T.meta_for(tmdb_id=feature.get('tmdb_id') or '', imdb_id=feature.get('open_imdb') or '', media_type=feature.get('media') or 'movie',
                           language_override='en-US' if _english() else 'ar-SA') or {}
        logo = str(found.get('clearlogo') or '')
        if logo.lower().endswith('.svg'):
            logo = logo[:-4] + '.png'
        feature['logo'] = logo
        if not feature.get('plot'):
            feature['plot'] = _story(found.get('overview') or found.get('plot') or '')
        if found.get('genres') and feature.get('meta'):
            genres = [str(g) for g in found['genres'][:2] if g]
            if genres:
                feature['meta'] = '   ·   '.join([' · '.join(genres)] + feature['meta'].split('   ·   '))
    except Exception:
        pass
    return feature.get('logo') or ''


# ------------------------------------------------------------- the Home's own
def slides():
    """The Home's saved titles that have a backdrop, shuffled."""
    from .. import watch_sources as W
    if W.config().get('personal_only'):
        return [r for r in W.features(enrich=False) if chosen_media() == 'both' or r['media'] == chosen_media()]
    seen, out = set(), []
    wanted = chosen_media()
    for page in ('all', 'movie', 'series'):
        try:
            with open(os.path.join(_profile(), 'home_%s.json' % page), 'r', encoding='utf-8') as handle:
                data = json.load(handle) or {}
        except Exception:
            continue
        for row in data.get('rows') or []:
            if not isinstance(row, dict):
                continue
            for tile in row.get('tiles') or []:
                if not isinstance(tile, dict) or tile.get('kind') != 'work':
                    continue
                fanart = str(tile.get('fanart') or '')
                if not fanart or fanart == tile.get('poster'):
                    continue
                key = tile.get('imdb_id') or tile.get('tmdb_id') or fanart
                if key in seen:
                    continue
                seen.add(key)
                media = tile.get('media_type') or ''
                kind = 'movie' if media == 'movie' else 'series'
                if wanted != 'both' and media and kind != wanted:
                    continue
                meta = []
                if tile.get('year'):
                    meta.append(str(tile.get('year')))
                if media in _TYPES:
                    meta.append(_tr(_TYPES[media]))
                genres = [str(g) for g in (tile.get('genres') or []) if g][:2]
                if genres:
                    meta.append(' / '.join(genres))
                logo = str(tile.get('clearlogo') or '')
                if logo.lower().split('?', 1)[0].endswith('.svg'):
                    logo = ''
                out.append({
                    'fanart': fanart, 'logo': logo,
                    'title': str(tile.get('show') or tile.get('title') or ''),
                    'meta': '   •   '.join(meta), 'row': str(row.get('title') or ''),
                    'plot': _story(tile.get('plot')),
                    # what opens the title page (OK)
                    'open_tmdb': str(tile.get('tmdb_id') or ''), 'open_imdb': str(tile.get('imdb_id') or ''),
                    'media': kind if media else '',
                })
    random.shuffle(out)
    return out[:MAX_SLIDES]




# ------------------------------------------------------------- opening a title
_OPENED_AT = 0.0


def open_title(item):
    """Dex Hub's page of a title the screensaver shows (the skin's own page
    on skin.dexhub and Arctic Fuse 3 with Dex Hub's pages, else the add-on's).
    False when the title has no TMDb or IMDb id."""
    tmdb_id = str(item.get('tmdb_id') or item.get('open_tmdb') or '')
    imdb_id = str(item.get('open_imdb') or '')
    if not tmdb_id and not imdb_id:
        return False
    query = {'m': item.get('media') or 'movie', 'title': item.get('title') or ''}
    if tmdb_id:
        query['tmdb'] = tmdb_id
    if imdb_id:
        query['imdb'] = imdb_id
    try:
        from ..skinui.items import title_target
        url = title_target(query)
    except Exception:
        url = ''
    if not url:
        from urllib.parse import urlencode
        url = 'plugin://%s/?%s' % (ADDON_ID, urlencode([('action', 'home_details')] + sorted(query.items())))
    global _OPENED_AT
    if 0 <= time.time() - _OPENED_AT < 2.0:
        return True             # v5.10.182: one page per OK
    _OPENED_AT = time.time()
    _log('opening a title (%s %s)' % (query['m'], tmdb_id or imdb_id))
    xbmc.executebuiltin('RunPlugin(%s)' % url)
    return True


# ------------------------------------------------------------- the window
def _when(day, today, english):
    delta = (day - today).days
    if delta == 0:
        return _tr('اليوم')
    if delta == 1:
        return _tr('غداً')
    return (_DAYS_EN if english else _DAYS_AR)[day.weekday()]


def _relative(day, today):
    delta = (day - today).days
    if delta <= 0:
        return _tr('اليوم')
    if delta == 1:
        return _tr('بعد يوم')
    if delta == 2:
        return _tr('بعد يومين')
    return _tr('بعد %d أيام') % delta


def _ordered(features, order):
    """v5.10.334: shuffled, or newest: what you are watching first, then your
    lists, then TMDb's (each in its own order)."""
    features = list(features or [])
    if order == 'newest':
        rank = {'continue': 0, 'mine': 1}
        return sorted(features, key=lambda f: rank.get(f.get('kind'), 2))
    random.shuffle(features)
    return features


def _pill(day, today):
    """Today, Tomorrow, In 3 days."""
    delta = (day - today).days
    if delta <= 0:
        return _tr('اليوم')
    if delta == 1:
        return _tr('غداً')
    return (('In %d days' if _english() else 'بعد %d أيام') % delta) if delta > 2 else _tr('بعد يومين')


class Saver(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(Saver, self).__init__(*args)
        try:
            from .. import watch_sources as W
            self.look = dict(W.look())
        except Exception:
            self.look = {}
        self.style = self.look.get('style') if self.look.get('style') in ('cinema', 'spotlight', 'upnext') else 'cinema'
        try:
            self.slide = max(5.0, float(self.look.get('slide') or SLIDE))
        except (TypeError, ValueError):
            self.slide = SLIDE
        self.slide_at = time.time()
        self.features = _ordered(kwargs.get('slides') or [], self.look.get('order'))
        self.home = list(self.features)
        self.brand = kwargs.get('brand') or ''
        self.browse = bool(kwargs.get('browse'))
        self.done = threading.Event()
        self.skip = threading.Event()
        self.slot = ''
        self.index = -1
        self.calendar = []
        self.day = None             # the chosen day (None: the coming week)
        self.active = 0
        self.aim = 'feature'        # what OK opens: the title on the left, or a coming one
        self._lock = threading.RLock()

    # ------------------------------------------------------------ start
    def onInit(self):
        english = _english()
        today = datetime.date.today()
        try:
            from ..skinui import preload      # v5.10.365: the Home's pictures, cached meanwhile
            preload.feed(self, self.done)
        except Exception:
            pass
        self.setProperty('ss.brand', self.brand)
        self.setProperty('ss.tagline', _tr('سينما، في استراحة'))
        self.setProperty('ss.scene', _tr('لحظة هدوء، قبل الحكاية.'))
        self.setProperty('ss.mark', _tr('بيانات TMDb'))
        from .. import watch_sources as W
        labels = [r['label'] for r in W.config()['sources'] if r.get('display', True)]
        self.setProperty('ss.sources', ' · '.join(labels)[:160])
        self.setProperty('ss.source.label', 'Personal sources' if _english() else 'مصادرك الشخصية')
        self.setProperty('ss.heading', _tr('على موعد'))
        self.setProperty('ss.count.label', _tr('الوقت المتبقي للعرض'))
        self.setProperty('ss.month', '%s %d' % ((_MONTHS_EN if english else _MONTHS_AR)[today.month - 1], today.year))
        # v5.10.357: no key hints at the bottom of the page any more
        self.setProperty('ss.aim', 'feature' if self.browse else '')
        # v5.10.334: the style, the clock, the day, and the Up next lists
        self.setProperty('ss.style', self.style)
        self.setProperty('ss.clock.on', '' if self.look.get('clock') is False else '1')
        self.setProperty('ss.dayname', (_DAYS_EN if english else _DAYS_AR)[today.weekday()])
        self.setProperty('ss.date', '%d %s' % (today.day, (_MONTHS_EN if english else _MONTHS_AR)[today.month - 1]))
        self.setProperty('ss.week.label', _tr('هذا الأسبوع'))
        self.setProperty('ss.yourweek', _tr('أسبوعك'))
        self.setProperty('ss.upnext', _tr('التالي لك'))
        self.setProperty('ss.cw', _tr('متابعة المشاهدة'))
        self._fill_continue()
        if self.features:
            self._show('a', self._next())
        else:
            self.setProperty('ss.empty', '1')
        threading.Thread(target=self._load, name='DexHub-ss-cinema', daemon=True).start()
        threading.Thread(target=self._loop, name='DexHub-screensaver', daemon=True).start()
        threading.Thread(target=self._clock, name='DexHub-ss-clock', daemon=True).start()

    def _load(self):
        try:
            features, calendar, fresh = cinema()
            self._take(features, calendar)
            if not fresh and not self.done.is_set():
                features, calendar, _ = cinema(refresh=True)
                self._take(features, calendar)
        except Exception as exc:
            _log('load failed (%s)' % exc)

    def _take(self, features, calendar):
        if self.done.is_set():
            return
        swap = False
        with self._lock:
            if features and [f.get('tmdb_id') for f in features] != getattr(self, '_tmdb_ids', None):
                # TMDb's titles first, the Home's own after them
                self._tmdb_ids = [f.get('tmdb_id') for f in features]
                self.features = _ordered(features, self.look.get('order')) + self.home[:10]
                self.index = -1
                self.setProperty('ss.empty', '')
                if not self.slot:
                    self._show('a', self._next())
                else:
                    swap = True
            if calendar:
                self.calendar = list(calendar)
        self._fill_week()
        self._fill_releases()
        if swap:
            self._swap()

    # ------------------------------------------------------------ the slides
    def _next(self, step=1):
        self.index = (self.index + step) % len(self.features)
        feature = self.features[self.index]
        if feature.get('tmdb_id'):
            _logo_for(feature)
        return feature

    def _fill(self, slot, feature):
        values = {'fanart': feature.get('fanart'), 'logo': feature.get('logo'), 'title': feature.get('title'),
                  'meta': feature.get('meta'), 'plot': feature.get('plot'),
                  'row': feature.get('section') or feature.get('row')}
        for key, value in values.items():
            self.setProperty('ss.%s.%s' % (slot, key), value or '')
        self.setProperty('ss.%s.long' % slot, '1' if len(feature.get('title') or '') > 24 else '')
        self.setProperty('ss.%s.kb' % slot, random.choice(('1', '2')))
        # v5.10.334: the source chip, the progress, the chips of the styles
        kind = feature.get('kind') or ('mine' if feature.get('section') else 'home')
        prog = int(feature.get('prog') or 0)
        self.setProperty('ss.%s.kind' % slot, kind)
        self.setProperty('ss.%s.chip' % slot, feature.get('chip') or feature.get('section') or feature.get('row') or '')
        self.setProperty('ss.%s.prog' % slot, str(prog) if prog > 0 else '')
        self.setProperty('ss.%s.left' % slot, feature.get('left') or '')
        self.setProperty('ss.%s.ep' % slot, feature.get('ep') or '')
        progress_line = '  ·  '.join(p for p in (feature.get('ep') or '', feature.get('left') or '') if p)
        self.setProperty('ss.%s.progline' % slot, progress_line)
        chips = [c.strip() for c in (feature.get('meta') or '').replace('•', '·').split('·') if c.strip()]
        if feature.get('rating') and not any('★' in c for c in chips):
            chips.append('★ ' + feature['rating'])
        for n in range(4):
            self.setProperty('ss.%s.c%d' % (slot, n + 1), chips[n] if n < len(chips) else '')
        badge = {'continue': _tr('متابعة المشاهدة'), 'mine': _tr('جديد في قائمتك')}.get(kind) or (
            feature.get('section') or '')
        self.setProperty('ss.%s.badge' % slot, badge)
        if self.features:
            self.setProperty('ss.dot', str(max(0, self.index) % DOTS))
            self.setProperty('ss.dots', str(min(DOTS, len(self.features))))

    def _show(self, slot, feature):
        self._fill(slot, feature)
        self.setProperty('ss.%s.zoom' % slot, '1')
        self.setProperty('ss.slot', slot)
        self.slot = slot

    def _swap(self, step=1):
        with self._lock:
            if not self.features:
                return
            other = 'b' if self.slot == 'a' else 'a'
            old = self.slot
            self.setProperty('ss.%s.zoom' % other, '')
            self._fill(other, self._next(step))
        if self.done.wait(0.5):
            return
        self.setProperty('ss.%s.zoom' % other, '1')
        self.setProperty('ss.slot', other)
        self.slot = other
        if self.done.wait(FADE):
            return
        self.setProperty('ss.%s.zoom' % old, '')

    # ------------------------------------------------------------ v5.10.180
    @staticmethod
    def _resolve(entry):
        """A coming title from Floppy or Trakt often has no TMDb id: it is
        found by its name (a series first for an episode)."""
        if entry.get('tmdb_id') or entry.get('_resolved'):
            return entry.get('tmdb_id') or ''
        entry['_resolved'] = True
        try:
            from .. import tmdb_direct as T
            want = 'movie' if entry.get('kind') == 'movie' else 'tv'
            found = [r for r in (T.search_multi(entry.get('title') or '', limit=10) or [])
                     if r.get('tmdb_id') and r.get('media_type') in ('movie', 'series')]
            pick = next((r for r in found if str(r.get('media_type') or '') in
                         ((want, 'series') if want == 'tv' else (want,))), None) or (found[0] if found else None)
            if pick:
                entry['tmdb_id'] = str(pick.get('tmdb_id'))
                mt = str(pick.get('media_type') or '')
                entry['media'] = 'movie' if mt == 'movie' else 'series'
        except Exception:
            pass
        return entry.get('tmdb_id') or ''

    def _entry_feature(self, entry):
        """A coming title as the main title (its backdrop, logo and story
        from TMDb, kept on the entry)."""
        feature = entry.get('_feature')
        if feature is None:
            self._resolve(entry)
            when = ''
            try:
                day = datetime.date.fromisoformat(entry['date'])
                when = _when(day, datetime.date.today(), _english())
            except Exception:
                pass
            feature = {'tmdb_id': entry.get('tmdb_id') or '', 'media': entry.get('media') or 'movie',
                       'title': entry.get('title') or '', 'fanart': '', 'logo': '', 'plot': '',
                       'meta': '   ·   '.join(p for p in (entry.get('detail') or '', when) if p),
                       'section': _tr('القادم'), '_logo_done': True}
            try:
                from .. import tmdb_direct as T
                found = T.meta_for(tmdb_id=feature['tmdb_id'], media_type=feature['media'],
                                   title='' if feature['tmdb_id'] else feature['title'],
                                   language_override='en-US' if _english() else 'ar-SA') or {}
                feature['fanart'] = found.get('fanart') or entry.get('poster') or ''
                logo = str(found.get('clearlogo') or '')
                feature['logo'] = logo[:-4] + '.png' if logo.lower().endswith('.svg') else logo
                feature['plot'] = _story(found.get('overview') or '')
            except Exception:
                feature['fanart'] = entry.get('poster') or ''
            entry['_feature'] = feature
        return feature

    def _show_entry(self, token):
        """The chosen coming title takes the main place (a moment after the
        cursor rests on it)."""
        if self.done.wait(0.35) or token != self._aim_token:
            return
        shown = getattr(self, '_entries_shown', []) or []
        if self.aim != 'release' or not shown:
            return
        entry = shown[min(self.active, len(shown) - 1)]
        feature = self._entry_feature(entry)
        if token != self._aim_token or self.done.is_set():
            return
        with self._lock:
            other = 'b' if self.slot == 'a' else 'a'
            old = self.slot
            self.setProperty('ss.%s.zoom' % other, '')
            self._fill(other, feature)
        if self.done.wait(0.4) or token != self._aim_token:
            return
        self.setProperty('ss.%s.zoom' % other, '1')
        self.setProperty('ss.slot', other)
        self.slot = other
        if self.done.wait(FADE):
            return
        self.setProperty('ss.%s.zoom' % old, '')

    def _aimed(self):
        self._aim_token = getattr(self, '_aim_token', 0) + 1
        if self.aim == 'release':
            threading.Thread(target=self._show_entry, args=(self._aim_token,), daemon=True,
                             name='DexHub-ss-entry').start()

    def _loop(self):
        monitor = xbmc.Monitor()
        rest = self.slide
        while not self.done.is_set() and not monitor.abortRequested():
            started = time.time()
            self.slide_at = started
            while time.time() - started < rest:
                if self.done.wait(0.5):
                    return
                if self.skip.is_set():
                    break
                # v5.10.334: the slide's timer bar
                self.setProperty('ss.tick', str(min(100, int(100 * (time.time() - started) / rest))))
            if self.aim == 'release' and not self.skip.is_set():
                continue            # a coming title is on show: the slides wait
            step = -1 if getattr(self, '_back_step', False) else 1
            self._back_step = False
            self.skip.clear()
            # the next picture loads into the hidden slot before it fades in
            self._swap(step)
            if not self.browse:
                self._rotate_release()

    # ------------------------------------------------------------ the rail
    def _week(self):
        today = datetime.date.today()
        return [today + datetime.timedelta(days=n) for n in range(7)]

    def _fill_week(self):
        english = _english()
        marked = set(e['date'] for e in self.calendar)
        per_day = {}
        for entry in self.calendar:
            per_day[entry['date']] = per_day.get(entry['date'], 0) + 1
        items = []
        # right to left: today on the right, as Arabic reads
        days = self._week()
        if not english:
            days = list(reversed(days))
        for day in days:
            li = xbmcgui.ListItem(label=str(day.day), offscreen=True)
            li.setProperty('weekday', (_WEEK_EN if english else _WEEK_AR)[day.weekday()])
            li.setProperty('has', '1' if day.isoformat() in marked else '')
            li.setProperty('sel', '1' if self.day == day else '')
            li.setProperty('iso', day.isoformat())
            # v5.10.334: today in gold, a dot per title (two at most)
            li.setProperty('today', '1' if day == days[0 if english else -1] else '')
            li.setProperty('n', str(min(2, per_day.get(day.isoformat(), 0))))
            items.append(li)
        try:
            ctrl = self.getControl(WEEK_LIST)
            ctrl.reset()
            ctrl.addItems(items)
            if self.day is not None:
                for index, li in enumerate(items):
                    if li.getProperty('iso') == self.day.isoformat():
                        ctrl.selectItem(index)
        except Exception:
            pass

    def _entries(self):
        if self.day is None:
            return self.calendar[:3]
        return [e for e in self.calendar if e['date'] == self.day.isoformat()][:3]

    def _fill_releases(self):
        english = _english()
        today = datetime.date.today()
        entries = self._entries()
        if self.day is None:
            self.setProperty('ss.section', _tr('القادم هذا الأسبوع'))
        else:
            label = '%d %s' % (self.day.day, (_MONTHS_EN if english else _MONTHS_AR)[self.day.month - 1])
            self.setProperty('ss.section', ('%s  ·  %s' % (_tr('اليوم'), label)) if self.day == today else label)
        items = []
        for index, entry in enumerate(entries):
            try:
                day = datetime.date.fromisoformat(entry['date'])
            except Exception:
                continue
            li = xbmcgui.ListItem(label=entry.get('title') or '', label2=entry.get('detail') or '', offscreen=True)
            if entry.get('poster'):
                li.setArt({'poster': entry['poster']})
            li.setProperty('when', _when(day, today, english))
            li.setProperty('rel', _relative(day, today))
            li.setProperty('active', '1' if index == self.active else '')
            li.setProperty('sel', '1' if (self.browse and self.aim == 'release' and index == self.active) else '')
            li.setProperty('pill', _pill(day, today))       # v5.10.334
            li.setProperty('today', '1' if day == today else '')
            items.append(li)
        self.setProperty('ss.cal', '1' if self.calendar else '')
        week = [e for e in self.calendar if e.get('date', '') <= (today + datetime.timedelta(days=6)).isoformat()]
        self.setProperty('ss.coming', (('%d coming up' if english else '%d قادم') % len(week)) if week else '')
        shows = len([e for e in week if e.get('kind') != 'movie'])
        films = len(week) - shows
        parts = []
        if shows:
            parts.append(('%d shows' if english else '%d مسلسلات') % shows)
        if films:
            parts.append(('%d films' if english else '%d أفلام') % films)
        self.setProperty('ss.week.count', ' · '.join(parts))
        self._fill_timeline(week)
        self.setProperty('ss.none', '' if items else _tr('لا توجد عروض في هذا اليوم'))
        try:
            ctrl = self.getControl(RELEASE_LIST)
            ctrl.reset()
            ctrl.addItems(items)
        except Exception:
            pass
        self._entries_shown = entries
        self.active = min(self.active, max(0, len(entries) - 1))
        self._count_now()

    def _fill_timeline(self, week):
        """v5.10.334: Your week (the Up next style): five coming titles."""
        if self.style != 'upnext':
            return
        english = _english()
        today = datetime.date.today()
        items = []
        for entry in week[:5]:
            try:
                day = datetime.date.fromisoformat(entry['date'])
            except Exception:
                continue
            li = xbmcgui.ListItem(label=entry.get('title') or '', label2=entry.get('detail') or '', offscreen=True)
            if entry.get('poster'):
                li.setArt({'poster': entry['poster']})
            li.setProperty('day', _tr('اليوم') if day == today else (_WEEK_EN if english else _WEEK_AR)[day.weekday()])
            li.setProperty('num', str(day.day))
            li.setProperty('today', '1' if day == today else '')
            items.append(li)
        try:
            ctrl = self.getControl(TIMELINE_LIST)
            ctrl.reset()
            ctrl.addItems(items)
        except Exception:
            pass

    def _fill_continue(self):
        """v5.10.334: what you are watching (the Up next style), three cards."""
        if self.style != 'upnext':
            return
        try:
            from .. import watch_sources as W
            rows = W.continue_items(3)
        except Exception:
            rows = []
        items = []
        for row in rows:
            line = '  ·  '.join(p for p in (row.get('ep') or '', row.get('left') or '') if p)
            li = xbmcgui.ListItem(label=row.get('title') or '', label2=line, offscreen=True)
            li.setArt({'thumb': row.get('fanart') or row.get('poster') or '', 'clearlogo': row.get('logo') or ''})
            li.setProperty('prog', str(int(row.get('prog') or 0)))
            li.setProperty('pct', str(int(row.get('prog') or 0)) if row.get('prog') else '')
            items.append(li)
        self.setProperty('ss.cw.on', '1' if items else '')
        try:
            ctrl = self.getControl(CONTINUE_LIST)
            ctrl.reset()
            ctrl.addItems(items)
        except Exception:
            pass

    def _rotate_release(self):
        shown = getattr(self, '_entries_shown', []) or []
        if len(shown) > 1:
            self.active = (self.active + 1) % len(shown)
            self._mark_active()

    def _mark_active(self):
        try:
            ctrl = self.getControl(RELEASE_LIST)
            for index in range(ctrl.size()):
                item = ctrl.getListItem(index)
                item.setProperty('active', '1' if index == self.active else '')
                item.setProperty('sel', '1' if (self.browse and self.aim == 'release' and index == self.active) else '')
        except Exception:
            pass
        self.setProperty('ss.aim', self.aim if self.browse else '')
        self._count_now()

    def go_browse(self):
        """The screensaver becomes browsable (an arrow was pressed)."""
        if self.browse:
            return
        self.browse = True
        self._mark_active()

    def _count_now(self):
        shown = getattr(self, '_entries_shown', []) or []
        if not shown:
            self.setProperty('ss.count', '')
            self.setProperty('ss.count.title', '')
            return
        entry = shown[min(self.active, len(shown) - 1)]
        self.setProperty('ss.count.title', entry.get('title') or '')
        self.setProperty('ss.count.detail', entry.get('detail') or '')     # v5.10.334
        self.setProperty('ss.count.until', _tr('حتى %s') % (entry.get('title') or '') if not _english()
                         else 'Until %s' % (entry.get('title') or ''))
        try:
            day = datetime.date.fromisoformat(entry['date'])
        except Exception:
            self.setProperty('ss.count', '')
            return
        start = datetime.datetime.combine(day, datetime.time(0, 0))
        left = int((start - datetime.datetime.now()).total_seconds())
        if left <= 0:
            self.setProperty('ss.count', _tr('اليوم'))
            return
        days, rest = divmod(left, 86400)
        clock = '%02d:%02d:%02d' % (rest // 3600, rest % 3600 // 60, rest % 60)
        self.setProperty('ss.count', ('%s  %s' % (_tr('%d ي') % days, clock)) if days else clock)

    def _clock(self):
        # v5.10.183: the saver ends itself when Kodi asks the script to stop
        # or something starts playing (in browse mode Kodi's own end is not
        # taken: Kodi then killed the script after 5 s)
        monitor = xbmc.Monitor()
        player = xbmc.Player()
        playing = player.isPlaying()
        while not self.done.wait(1.0):
            if monitor.abortRequested():
                self.stop()
                return
            now_playing = player.isPlaying()
            if now_playing and not playing:
                self.stop()
                return
            playing = now_playing
            self._count_now()

    # ------------------------------------------------------------ keys
    def onAction(self, action):
        aid = action.getId()
        if aid == 203:      # Kodi reading its keymaps again
            return
        if not self.browse:
            # OK opens the title in front (v5.10.177). v5.10.179: the arrows
            # stay on the screensaver and browse it (the title on the left,
            # the coming titles, the days); the other keys end it
            if aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
                self.go_browse()
            else:
                target = self._front() if aid == A_SELECT else None
                self.stop()
                if target:
                    open_title(target)
                return
        if aid in A_BACK:
            self.stop()
            return
        english = _english()
        if self.style != 'cinema' and aid in (A_LEFT, A_RIGHT, A_UP, A_DOWN):
            # v5.10.334: Spotlight and Up next have no day rail: the arrows
            # go to the next or the previous title
            self.aim = 'feature'
            back = aid == A_UP or aid == (A_LEFT if english else A_RIGHT)
            self._back_step = back
            self.skip.set()
            return
        if aid in (A_LEFT, A_RIGHT):
            days = self._week()
            if self.day is None:
                self.day = days[0]
            else:
                later = (aid == A_LEFT) != english      # Left goes on in Arabic
                at = days.index(self.day) if self.day in days else 0
                at = max(0, min(len(days) - 1, at + (1 if later else -1)))
                self.day = days[at]
            self.active = 0
            self.aim = 'release'
            self._fill_week()
            self._fill_releases()
            self._mark_active()
            self._aimed()
        elif aid in (A_UP, A_DOWN):
            # v5.10.179: Up and Down go through the title on the left and the
            # coming titles; the one OK opens is marked
            shown = getattr(self, '_entries_shown', []) or []
            spots = len(shown) + 1                      # 0: the title on the left
            at = 0 if self.aim == 'feature' else self.active + 1
            at = (at + (1 if aid == A_DOWN else -1)) % spots
            back = self.aim == 'release' and at == 0
            if at == 0:
                self.aim = 'feature'
            else:
                self.aim, self.active = 'release', at - 1
            self._mark_active()
            self._aimed()
            if back:
                self.skip.set()         # back on the slides: the next one shows
        elif aid in A_SKIP:
            self.aim = 'feature'
            self._mark_active()
            self.skip.set()
        elif aid == A_SELECT:
            target = self._front()
            if target:
                self.stop()
                open_title(target)
            else:
                self.skip.set()

    def _front(self):
        """What OK opens: the coming title chosen with the arrows, else the
        title on the left."""
        if self.aim == 'release':
            # v5.10.181: the coming title on show, never the slide behind it
            shown = getattr(self, '_entries_shown', []) or []
            if shown:
                entry = shown[min(self.active, len(shown) - 1)]
                return entry if self._resolve(entry) else None
            return None
        with self._lock:
            if self.features and 0 <= self.index < len(self.features):
                feature = self.features[self.index]
                if feature.get('tmdb_id') or feature.get('open_tmdb') or feature.get('open_imdb'):
                    return feature
        return None

    def onClick(self, control_id):
        # v5.10.182: OK comes as a key and as a click; the key already opened
        # the title (two pages opening together crashed Kodi)
        if self.done.is_set():
            return
        target = self._front()
        self.stop()
        if target:
            open_title(target)

    def stop(self):
        if self.done.is_set():
            return
        self.done.set()
        try:
            self.close()
        except Exception:
            pass


class _Monitor(xbmc.Monitor):
    def __init__(self, window):
        super(_Monitor, self).__init__()
        self.window = window

    def onScreensaverDeactivated(self):
        # v5.10.179: an arrow makes it browsable; Kodi tells of the key
        # before the window hears it, so the end waits a moment
        window = self.window

        def later():
            if not window.browse:
                window.stop()
        timer = threading.Timer(0.5, later)
        timer.daemon = True
        timer.start()

    def onAbortRequested(self):
        self.window.stop()


def run(browse=False):
    """Kodi starts the screensaver (screensaver.py), or the user opens it to browse."""
    # v5.10.357: Your List (the 'upnext' key) and News have windows of their own
    try:
        from .. import watch_sources as W
        style = W.look().get('style')
    except Exception:
        style = ''
    if style in ('upnext', 'news'):
        from . import ss_windows
        return ss_windows.run(style, browse=browse)
    addon = _addon()
    path = addon.getAddonInfo('path')
    brand = os.path.join(path, 'resources', 'media', 'fanart.jpg')
    try:
        found = slides()
    except Exception:
        found = []
    window = Saver('dexhub_screensaver.xml', path, 'Default', '1080i', slides=found, brand=brand, browse=browse)
    monitor = _Monitor(window)
    try:
        window.doModal()
    finally:
        window.stop()
        del monitor
        del window


# ---------------------------------------------------------------- setup
_PREVIOUS = 'screensaver_previous'


def _rpc(method, params=None):
    try:
        raw = xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                                              'params': params or {}}))
        return (json.loads(raw) or {}).get('result')
    except Exception:
        return None


def current_mode():
    found = _rpc('Settings.GetSettingValue', {'setting': 'screensaver.mode'}) or {}
    return str(found.get('value') or '') if isinstance(found, dict) else ''


def enable():
    """Make Dex Hub Kodi's screensaver; the previous choice is kept for off()."""
    mode = current_mode()
    if mode and mode != ADDON_ID:
        try:
            with open(os.path.join(_profile(), _PREVIOUS), 'w') as handle:
                handle.write(mode)
        except Exception:
            pass
    return _rpc('Settings.SetSettingValue', {'setting': 'screensaver.mode', 'value': ADDON_ID}) is not None


def disable():
    previous = ''
    try:
        with open(os.path.join(_profile(), _PREVIOUS), 'r') as handle:
            previous = handle.read().strip()
    except Exception:
        pass
    if not previous or previous == ADDON_ID:
        previous = 'screensaver.xbmc.builtin.dim'
    return _rpc('Settings.SetSettingValue', {'setting': 'screensaver.mode', 'value': previous}) is not None


def preview():
    """Open it now to browse (the days, the titles; Back ends it)."""
    run(browse=True)


SECTION_NAMES = {'airing': 'يُعرض اليوم', 'netflix': 'جديد على Netflix', 'streaming_tv': 'مسلسلات جديدة على المنصات',
                 'streaming_movie': 'أفلام جديدة على المنصات', 'top_tv': 'مسلسلات الأعلى تقييماً',
                 'trending_tv': 'مسلسلات رائجة هذا الأسبوع', 'trending_movie': 'أفلام رائجة هذا الأسبوع',
                 'now_playing': 'في السينما الآن', 'popular_movie': 'أفلام شائعة', 'top_movie': 'أفلام الأعلى تقييماً'}
MEDIA_NAMES = {'both': 'أفلام ومسلسلات', 'movie': 'أفلام فقط', 'series': 'مسلسلات فقط'}


def _forget():
    """The kept titles go, so the next start reads the new choice."""
    try:
        os.remove(os.path.join(_profile(), 'ss_cinema.json'))
    except Exception:
        pass


def pick_sources(tr=None):
    """Which TMDb lists the screensaver shows (several)."""
    tr = tr or _tr
    keep = chosen_sections()
    names = [tr(SECTION_NAMES[k]) for k in SECTION_KEYS]
    # v5.10.360: named for what it is (the old title read "add-ons" in English)
    picked = xbmcgui.Dialog().multiselect(_t('قوائم TMDb في شاشة التوقف', 'TMDb lists in the screensaver'), names,
                                          preselect=[i for i, k in enumerate(SECTION_KEYS) if k in keep])
    if picked is None:
        return False
    keys = [SECTION_KEYS[i] for i in picked]
    # v5.10.335: none picked is none (it came back as the defaults)
    _addon().setSetting('screensaver_sources', ','.join(keys) if keys else 'none')
    _forget()
    return True


def pick_media(tr=None):
    tr = tr or _tr
    from .options import choose
    current = chosen_media()
    choice = choose(tr('شاشة التوقف تعرض'), [tr(MEDIA_NAMES[k]) for k in MEDIA_CHOICES],
                    preselect=MEDIA_CHOICES.index(current))
    if choice < 0 or MEDIA_CHOICES[choice] == current:
        return False
    _addon().setSetting('screensaver_media', MEDIA_CHOICES[choice])
    _forget()
    return True


def _t(ar, en):
    return en if _english() else ar


STYLE_NAMES = {'cinema': ('سينما', 'Cinema'), 'spotlight': ('سبوتلايت', 'Spotlight'),
               'upnext': ('Your List', 'Your List'), 'news': ('أخبار السينما', 'Film news')}


def _pick_style():
    from .options import choose
    from .. import watch_sources as W
    keys = list(W.STYLES)
    current = W.look().get('style') or 'cinema'
    choice = choose(_t('شكل شاشة التوقف', 'Screensaver style'), [_t(*STYLE_NAMES[k]) for k in keys],
                    preselect=keys.index(current) if current in keys else 0)
    if choice < 0:
        return
    W.set_look('style', keys[choice])
    if keys[choice] == 'upnext' and not W.look().get('yourlist'):
        from .ss_page import pick_yourlist
        pick_yourlist()
    _forget()


def setup_menu(tr=None):
    """The screensaver options (Home menu, Dex Hub settings). v5.10.360: each
    line names what it sets and shows its value; the lines follow the style."""
    from .options import choose
    from .. import watch_sources as W
    while True:
        on = current_mode() == ADDON_ID
        look = W.look()
        style = look.get('style') if look.get('style') in STYLE_NAMES else 'cinema'
        options = [('onoff', (_t('شاشة توقف Dex Hub', 'Dex Hub screensaver'), _t('شغالة', 'On') if on else _t('مطفأة', 'Off'))),
                   ('style', (_t('الشكل', 'Style'), _t(*STYLE_NAMES[style])))]
        if style == 'upnext':
            from .ss_windows import yourlist_source
            options.append(('yourlist', (_t('القائمة اللي تنعرض', 'The list it shows'),
                                         (yourlist_source() or {}).get('label') or _t('اختر قائمة', 'Pick a list'))))
        elif style == 'news':
            from .ss_windows import news_sources
            from . import ss_news
            names = [label for key, label, _k, _u in ss_news.SOURCES if key in news_sources()]
            options.append(('news', (_t('مصادر الأخبار', 'News sources'), ', '.join(names))))
        else:
            keep = chosen_sections()
            shown = [r for r in W.config()['sources'] if r.get('display', True)]
            options.append(('tmdb', (_t('قوائم TMDb', 'TMDb lists'),
                                     (_t('%d من %d', '%d of %d') % (len(keep), len(SECTION_KEYS))) if keep
                                     else _t('متوقفة', 'Off'))))
            options.append(('personal', (_t('قوائمك: Trakt و MDBList والكتالوجات ومتابعة المشاهدة',
                                            'Your lists: Trakt, MDBList, catalogs, Continue Watching'),
                                         _t('%d معروضة', '%d shown') % len(shown))))
        options.append(('media', (_t('تعرض', 'Shows'), _t(*{'both': ('أفلام ومسلسلات', 'Films and series'),
                                                               'movie': ('أفلام فقط', 'Films only'),
                                                               'series': ('مسلسلات فقط', 'Series only')}[chosen_media()]))))
        options.append(('page', (_t('كل الإعدادات', 'All settings'), _t('الأشكال والمصادر والتوقيت', 'Styles, sources, timing'))))
        options.append(('preview', (_t('افتحها الآن', 'Preview now'), '')))
        subtitle = _t('شاشة التوقف شغالة', 'The screensaver is on') if on else _t('شاشة التوقف مطفأة', 'The screensaver is off')
        choice = choose(_t('شاشة التوقف', 'Screensaver'), [label for _key, label in options], subtitle=subtitle)
        if choice < 0:
            return
        key = options[choice][0]
        if key == 'onoff':
            ok = disable() if on else enable()
            message = (_t('رجعت شاشة التوقف السابقة', 'The previous screensaver is back') if on
                       else _t('شاشة توقف Dex Hub شغالة', 'The Dex Hub screensaver is on')) if ok \
                else _t('تعذر تغيير شاشة التوقف', 'The screensaver could not be changed')
            try:
                xbmcgui.Dialog().notification('Dex Hub', message, xbmcgui.NOTIFICATION_INFO, 2500, sound=False)
            except Exception:
                pass
        elif key == 'style':
            _pick_style()
        elif key == 'yourlist':
            from .ss_page import pick_yourlist
            pick_yourlist()
        elif key == 'news':
            from .ss_windows import pick_news_sources
            pick_news_sources()
        elif key == 'tmdb':
            pick_sources()
        elif key == 'personal':
            from .. import watch_sources
            watch_sources.menu()
        elif key == 'media':
            pick_media()
        elif key == 'page':
            from . import ss_page
            ss_page.open_page()
        elif key == 'preview':
            preview()
            return
