# -*- coding: utf-8 -*-
"""The screensaver's Your List and News styles (v5.10.357).

Your List (style key 'upnext', kept so the saved choice carries over): the
titles of one list the user picks (Continue Watching, favorites, a Trakt or
MDBList list, a Silo list or a Home catalog) on a carousel: the title in
front fills the middle, the one before and the one after show at the edges,
the background is its backdrop. It turns by itself; Left and Right turn it,
OK opens the title's page.

News: film news (IMDb and the trade press, see ss_news) with the story's
picture behind, the film's TMDb poster when the headline names one, and the
next stories on a strip. Left and Right go through them, OK reads the whole
story, Back closes the story and then the screensaver.

Layouts: resources/skins/*/1080i/dexhub_ss_yourlist.xml and
dexhub_ss_news.xml.
  Your List: wraplist 400 (label title, label2 meta; art thumb, fanart,
  poster, clearlogo; props plot, prog, progline, chip); window props
  ss.assets, ss.heading, ss.listname, ss.empty, ss.clock.on, ss.dayname,
  ss.date.
  News: wraplist 510 (label headline, label2 source and age; art thumb,
  fanart, poster; props summary, match, source, age); window props
  ss.heading, ss.more, ss.reader ('1' while a story is open), ss.r.title,
  ss.r.source, ss.r.body, ss.r.image, ss.r.poster, ss.r.loading; textbox
  520 the story, button 531 holds the focus while it is open.
"""
import datetime
import hashlib
import io
import os
import re
import threading
import time

import xbmc
import xbmcgui

from . import screensaver as SS
from . import ss_news as N

YOUR_LIST = 400
NEWS_LIST, NEWS_TEXT, NEWS_HOLD = 510, 520, 531
GRACE = 6.0             # seconds after a key before the carousel turns by itself again


def _t(ar, en):
    return en if SS._english() else ar


def _assets():
    import xbmcaddon
    return xbmcaddon.Addon(SS.ADDON_ID).getAddonInfo('path')


class _Base(xbmcgui.WindowXMLDialog):
    LIST = 0

    def __init__(self, *args, **kwargs):
        super(_Base, self).__init__(*args)
        self.browse = bool(kwargs.get('browse'))
        self.done = threading.Event()
        self.touched = 0.0
        self.gen = 0            # bumped by every refill (the art threads start over)
        try:
            from .. import watch_sources as W
            self.look = dict(W.look())
        except Exception:
            self.look = {}
        try:
            self.slide = max(5.0, float(self.look.get('slide') or SS.SLIDE))
        except (TypeError, ValueError):
            self.slide = SS.SLIDE

    def _common(self):
        english = SS._english()
        today = datetime.date.today()
        self.setProperty('ss.assets', _assets())
        self.setProperty('ss.clock.on', '' if self.look.get('clock') is False else '1')
        self.setProperty('ss.dayname', (SS._DAYS_EN if english else SS._DAYS_AR)[today.weekday()])
        self.setProperty('ss.date', '%d %s' % (today.day, (SS._MONTHS_EN if english else SS._MONTHS_AR)[today.month - 1]))

    def _start(self, *jobs):
        for name, job in jobs + (('clock', self._watch),):
            threading.Thread(target=job, name='DexHub-ss-' + name, daemon=True).start()

    def _watch(self):
        """The screensaver ends itself when Kodi stops or a video starts."""
        monitor = xbmc.Monitor()
        player = xbmc.Player()
        playing = player.isPlaying()
        while not self.done.wait(1.0):
            if monitor.abortRequested():
                self.stop()
                return
            now = player.isPlaying()
            if now and not playing:
                self.stop()
                return
            playing = now

    def _turn(self):
        """The carousel turns by itself, unless a key was pressed a moment ago."""
        while not self.done.wait(0.5):
            if self.done.wait(self.slide):
                return
            if self._paused() or time.time() - self.touched < GRACE + self.slide / 2:
                continue
            try:
                if self.getControl(self.LIST).size() > 1:
                    xbmc.executebuiltin('Control.Move(%d,1)' % self.LIST)
            except Exception:
                pass

    def _paused(self):
        return False

    def _selected(self):
        try:
            return self.getControl(self.LIST).getSelectedItem()
        except Exception:
            return None

    def go_browse(self):
        self.browse = True

    def stop(self):
        if self.done.is_set():
            return
        self.done.set()
        try:
            self.close()
        except Exception:
            pass


# ------------------------------------------------------------------ Your List
def yourlist_source():
    """The list Your List shows: the one picked, else Continue Watching, else the first."""
    from .. import watch_sources as W
    cfg = W.config()
    wanted = str(cfg['look'].get('yourlist') or '')
    sources = cfg.get('sources') or []
    return (next((s for s in sources if s.get('id') == wanted), None)
            or next((s for s in sources if s.get('kind') == 'continue'), None)
            or (sources[0] if sources else None))


def yourlist_rows(source):
    from .. import watch_sources as W
    if not source:
        return []
    media = SS.chosen_media()
    return [r for r in W.snapshot().get('rows') or []
            if source['id'] in (r.get('source_ids') or []) and (media == 'both' or r.get('media') == media)]


BLUR_KEEP = 150         # blurred backdrops kept in the profile


def _blur_folder():
    return os.path.join(SS._profile(), 'ss_blur')


def _blur_path(url):
    return os.path.join(_blur_folder(), hashlib.sha1(url.encode('utf-8')).hexdigest()[:16] + '.jpg')


def blurred(url):
    """v5.10.360: a small blurred copy of a backdrop for Your List's page
    (PIL, script.module.pil), kept in the profile; '' when it cannot be made
    (the backdrop itself stays then)."""
    url = str(url or '')
    if not url or url.startswith('image://'):
        return ''
    path = _blur_path(url)
    if os.path.exists(path):
        return path
    try:
        from PIL import Image, ImageFilter
    except Exception:
        return ''
    src = url.split('|', 1)[0]
    match = re.match(r'^(https?://image\.tmdb\.org/t/p/)[^/]+(/.+)$', src)
    if match:
        src = match.group(1) + 'w300' + match.group(2)      # a small copy is all a blur needs
    try:
        if src.startswith('http'):
            picture = Image.open(io.BytesIO(N.fetch(src, 8)))
        else:
            import xbmcvfs
            picture = Image.open(xbmcvfs.translatePath(src))
        picture = picture.convert('RGB')
        picture.thumbnail((480, 270))
        picture = picture.filter(ImageFilter.GaussianBlur(9))
        folder = _blur_folder()
        os.makedirs(folder, exist_ok=True)
        picture.save(path + '.tmp', 'JPEG', quality=86)
        os.replace(path + '.tmp', path)
        kept = sorted((os.path.join(folder, n) for n in os.listdir(folder) if n.endswith('.jpg')),
                      key=os.path.getmtime)
        for old in kept[:-BLUR_KEEP]:
            os.remove(old)
        return path
    except Exception as exc:
        SS._log('blur failed (%s)' % exc.__class__.__name__)
        return ''


def _meta(row):
    parts = [_t('مسلسل', 'Series') if row.get('media') == 'series' else _t('فيلم', 'Movie')]
    if row.get('year'):
        parts.append(str(row['year']))
    return '   ·   '.join(parts)


class YourList(_Base):
    LIST = YOUR_LIST

    def __init__(self, *args, **kwargs):
        super(YourList, self).__init__(*args, **kwargs)
        self.source = None
        self.rows = []
        self._sig = None

    def onInit(self):
        self._common()
        self.source = yourlist_source()
        self.setProperty('ss.heading', 'Your List')
        self.setProperty('ss.listname', (self.source or {}).get('label') or '')
        self._fill(yourlist_rows(self.source))
        self._start(('yourlist-load', self._load), ('yourlist-turn', self._turn),
                    ('yourlist-art', self._enrich))

    def _fill(self, rows):
        sig = [r.get('key') for r in rows]
        if sig == self._sig:
            return
        self._sig = sig
        self.gen += 1
        self.rows = [dict(r) for r in rows[:60]]
        items = []
        for row in self.rows:
            fanart = row.get('fanart') or ''
            poster = row.get('poster') or ''
            li = xbmcgui.ListItem(label=row.get('title') or '', label2=_meta(row), offscreen=False)
            back = fanart or poster
            done = _blur_path(back) if back and os.path.exists(_blur_path(back)) else ''
            li.setArt({'thumb': back, 'fanart': back, 'blur': done or back, 'poster': poster,
                       'clearlogo': row.get('logo') or ''})
            li.setProperty('plot', SS._story(row.get('plot')))
            prog = int(row.get('prog') or 0)
            li.setProperty('prog', str(prog) if prog > 0 else '')
            li.setProperty('progline', '  ·  '.join(p for p in (row.get('ep') or '', row.get('left') or '') if p))
            li.setProperty('chip', ' · '.join(row.get('sources') or []))
            items.append(li)
        try:
            ctrl = self.getControl(YOUR_LIST)
            ctrl.reset()
            ctrl.addItems(items)
        except Exception:
            return
        self.setProperty('ss.empty', '' if items else _t('القائمة فاضية الحين، تنعرض أول ما تتحدث',
                                                       'This list is empty for now; it shows once it refreshes'))
        if items:
            self.setFocusId(YOUR_LIST)
        else:
            self.setFocusId(100)

    def _load(self):
        """The list read again in the background (only when its time came),
        and again every quarter hour while the screensaver stays up."""
        while not self.done.is_set():
            try:
                from .. import watch_sources as W
                W.refresh()
            except Exception as exc:
                SS._log('your list refresh failed (%s)' % exc)
            if self.done.is_set():
                return
            rows = yourlist_rows(self.source)
            if rows and (not self.rows or time.time() - self.touched > GRACE):
                self._fill(rows)
            if self.done.wait(900):
                return

    def _enrich(self):
        """Backdrops and logos for the titles near the front (TMDb, once each)."""
        from .. import reminders as R
        done, gen = set(), -1
        lookups = 0
        while not self.done.wait(1.0):
            try:
                ctrl = self.getControl(YOUR_LIST)
                size = ctrl.size()
                at = ctrl.getSelectedPosition()
            except Exception:
                return
            if gen != self.gen:
                done, gen = set(), self.gen
            rows = self.rows
            if not size or len(rows) != size:
                continue
            for index in [(at + d) % size for d in (0, 1, -1, 2, -2, 3)]:
                if index in done or self.done.is_set() or gen != self.gen:
                    continue
                done.add(index)
                try:
                    row = rows[index]
                    li = ctrl.getListItem(index)
                except Exception:
                    continue
                art = {}
                if not row.get('fanart') and lookups < 40:
                    lookups += 1
                    try:
                        tmdb = row.get('tmdb') or R._tmdb_of(row.get('imdb'), row['media'])
                        data = R._show(tmdb) if row['media'] == 'series' else R._movie(tmdb)
                        row['tmdb'] = tmdb or row.get('tmdb')
                        row['fanart'] = R._art(data.get('backdrop'), 'backdrop')
                        if row['fanart']:
                            art.update(thumb=row['fanart'], fanart=row['fanart'], blur=row['fanart'])
                    except Exception:
                        pass
                # v5.10.360: the page behind the carousel, blurred
                soft = blurred(row.get('fanart') or row.get('poster'))
                if soft:
                    art['blur'] = soft
                if not row.get('logo') and (row.get('tmdb') or row.get('imdb')):
                    feature = {'tmdb_id': row.get('tmdb') or '', 'open_imdb': row.get('imdb') or '',
                               'media': row.get('media') or 'movie', 'meta': ''}
                    row['logo'] = SS._logo_for(feature)
                    if row['logo']:
                        art['clearlogo'] = row['logo']
                    if not row.get('plot') and feature.get('plot'):
                        row['plot'] = feature['plot']
                        li.setProperty('plot', feature['plot'])
                if art and gen == self.gen:
                    li.setArt(art)

    def onAction(self, action):
        aid = action.getId()
        if aid == 203:
            return
        self.touched = time.time()
        if aid in (SS.A_LEFT, SS.A_RIGHT, SS.A_UP, SS.A_DOWN):
            self.go_browse()        # the list turns by itself (it has the focus)
            return
        if aid == SS.A_SELECT:
            self._open()
            return
        if self.browse and aid not in SS.A_BACK:
            return
        self.stop()

    def onClick(self, control_id):
        self._open()

    def _open(self):
        if self.done.is_set():
            return
        item = self._selected()
        index = -1
        try:
            index = self.getControl(YOUR_LIST).getSelectedPosition()
        except Exception:
            pass
        row = self.rows[index] if 0 <= index < len(self.rows) else None
        self.stop()
        if row and item is not None:
            SS.open_title({'tmdb_id': row.get('tmdb') or '', 'open_imdb': row.get('imdb') or '',
                           'media': row.get('media') or 'movie', 'title': row.get('title') or ''})


# ------------------------------------------------------------------ News
def news_sources():
    import xbmcaddon
    try:
        raw = (xbmcaddon.Addon(SS.ADDON_ID).getSetting('screensaver_news') or '').strip()
    except Exception:
        raw = ''
    keys = [k for k in raw.split(',') if k in N.SOURCE_KEYS]
    return keys or list(N.DEFAULT_SOURCES)


def pick_news_sources():
    import xbmcaddon
    keep = news_sources()
    names = [label for _key, label, _kind, _url in N.SOURCES]
    picked = xbmcgui.Dialog().multiselect(_t('مصادر الأخبار', 'News sources'), names,
                                          preselect=[i for i, k in enumerate(N.SOURCE_KEYS) if k in keep])
    if picked is None:
        return False
    keys = [N.SOURCE_KEYS[i] for i in picked] or list(N.DEFAULT_SOURCES)
    xbmcaddon.Addon(SS.ADDON_ID).setSetting('screensaver_news', ','.join(keys))
    try:
        os.remove(os.path.join(SS._profile(), 'ss_news.json'))
    except Exception:
        pass
    return True


def _match(title):
    """The film or series a headline names: (title, year, poster, backdrop, tmdb, media)."""
    from .. import tmdb_direct as T
    for name in N.quoted_titles(title)[:2]:
        want = N._norm(name)
        try:
            found = T.search_multi(name, limit=8) or []
        except Exception:
            found = []
        for row in found:
            if row.get('media_type') not in ('movie', 'series') or not row.get('poster'):
                continue
            names = (N._norm(row.get('title')), N._norm(row.get('original_title')))
            if want and (want in names or (len(want) >= 6 and any(
                    n.startswith(want) or (len(n) >= 6 and want.startswith(n)) for n in names if n))):
                return row
    return None


class News(_Base):
    LIST = NEWS_LIST

    def __init__(self, *args, **kwargs):
        super(News, self).__init__(*args, **kwargs)
        self.slide = max(self.slide, 16.0)
        self.items = []
        self.reader = False
        self._reader_token = 0
        self._line = 0
        self.cache = os.path.join(SS._profile(), 'ss_news.json')

    def onInit(self):
        self._common()
        self.setProperty('ss.heading', _t('أخبار السينما', 'Film news'))
        self.setProperty('ss.more', _t('المزيد', 'More'))
        self.setProperty('ss.next', _t('أخبار أخرى', 'More stories'))
        try:
            items = N.cached(news_sources(), self.cache)     # never the network here: _load reads it
        except Exception:
            items = []
        if items:
            self._fill(items)
        else:
            self.setProperty('ss.empty', _t('جاري جلب الأخبار…', 'Loading the news…'))
            self.setFocusId(100)
        self._start(('news-load', self._load), ('news-turn', self._turn), ('news-art', self._enrich))

    def _fill(self, items):
        english = SS._english()
        self.gen += 1
        self.items = [dict(i) for i in items]
        rows = []
        for item in self.items:
            age = N.ago(item.get('ts'), english)
            li = xbmcgui.ListItem(label=item.get('title') or '', label2=' · '.join(p for p in (item.get('source'), age) if p),
                                  offscreen=False)     # edited later from the art thread: the GUI lock
            image = item.get('image') or ''
            li.setArt({'thumb': image, 'fanart': image})
            # three lines under the headline, ending on a word (v5.10.358)
            li.setProperty('summary', N.short(N._ELLIPSIS.sub('', item.get('summary') or ''), 250))
            li.setProperty('source', item.get('source') or '')
            li.setProperty('age', age)
            rows.append(li)
        try:
            ctrl = self.getControl(NEWS_LIST)
            ctrl.reset()
            ctrl.addItems(rows)
        except Exception:
            return
        self.setProperty('ss.empty', '' if rows else _t('تعذر جلب الأخبار الحين', 'The news could not be read right now'))
        if not self.reader:
            self.setFocusId(NEWS_LIST if rows else 100)

    def _load(self):
        """The news read when they are older than half an hour, and again
        every half hour while the screensaver stays up."""
        while not self.done.is_set():
            try:
                items, _fresh = N.load(news_sources(), self.cache, refresh=False, log=SS._log)
            except Exception as exc:
                SS._log('news load failed (%s)' % exc)
                items = []
            if self.done.is_set():
                return
            if items and [i.get('id') for i in items] != [i.get('id') for i in self.items]:
                if not self.items or (not self.reader and time.time() - self.touched > GRACE):
                    self._fill(items)
            elif not items and not self.items:
                self.setProperty('ss.empty', _t('تعذر جلب الأخبار الحين', 'The news could not be read right now'))
            if self.done.wait(N.TTL + 5):
                return

    def _enrich(self):
        """The poster of the film a headline names, and a picture for a story without one."""
        done, gen = set(), -1
        while not self.done.wait(1.0):
            try:
                ctrl = self.getControl(NEWS_LIST)
                size = ctrl.size()
                at = ctrl.getSelectedPosition()
            except Exception:
                return
            if gen != self.gen:
                done, gen = set(), self.gen
            items = self.items
            if not size or size != len(items):
                continue
            for index in [(at + d) % size for d in (0, 1, 2, 3)]:
                if index in done or self.done.is_set() or gen != self.gen:
                    continue
                done.add(index)
                try:
                    item = items[index]
                    li = ctrl.getListItem(index)
                except Exception:
                    continue
                found = None
                try:
                    found = _match(item.get('title'))
                except Exception:
                    found = None
                art = {}
                if found:
                    item['poster'] = found.get('poster') or ''
                    art['poster'] = item['poster']
                    name = found.get('title') or ''
                    li.setProperty('match', ' · '.join(p for p in (name, found.get('year') or '') if p))
                    if not item.get('image') and found.get('backdrop'):
                        item['image'] = found['backdrop']
                        art.update(thumb=item['image'], fanart=item['image'])
                if not item.get('image') and str(item.get('link') or '').startswith('http'):
                    try:
                        item['image'] = N.og_image(N.fetch(item['link'], 6))
                    except Exception:
                        item['image'] = ''
                    if item['image']:
                        art.update(thumb=item['image'], fanart=item['image'])
                if art and gen == self.gen:
                    li.setArt(art)

    def _paused(self):
        return self.reader

    # ------------------------------------------------------------ the story
    def _open_story(self):
        try:
            index = self.getControl(NEWS_LIST).getSelectedPosition()
        except Exception:
            return
        if not 0 <= index < len(self.items):
            return
        item = self.items[index]
        self.reader = True
        self._reader_token += 1
        token = self._reader_token
        self._line = 0
        self.setProperty('ss.r.title', item.get('title') or '')
        age = N.ago(item.get('ts'), SS._english())
        self.setProperty('ss.r.source', ' · '.join(p for p in (item.get('source'), age) if p))
        self.setProperty('ss.r.image', item.get('image') or '')
        self.setProperty('ss.r.poster', item.get('poster') or '')
        self.setProperty('ss.r.body', item.get('body') or item.get('summary') or '')
        self.setProperty('ss.r.loading', '' if len(item.get('body') or '') > 600 else '1')
        self.setProperty('ss.reader', '1')
        self.setFocusId(NEWS_HOLD)
        self._scroll(0)

        def job():
            body, image = N.full_story(item)
            if token != self._reader_token or self.done.is_set():
                return
            item['body'] = body
            if image and not item.get('image'):
                item['image'] = image
                self.setProperty('ss.r.image', image)
            self.setProperty('ss.r.body', body)
            self.setProperty('ss.r.loading', '')
            self._scroll(0)         # the new text starts at its top
        threading.Thread(target=job, name='DexHub-ss-story', daemon=True).start()

    def _close_story(self):
        self.reader = False
        self._reader_token += 1
        self.setProperty('ss.reader', '')
        self.setFocusId(NEWS_LIST if self.items else 100)

    def _scroll(self, line):
        # about 70 characters a line, 14 lines a page: Down stops at the end
        body = self.getProperty('ss.r.body') or ''
        last = max(0, sum(len(p) // 70 + 2 for p in body.split('\n\n')) - 12)
        self._line = max(0, min(line, last))
        try:
            self.getControl(NEWS_TEXT).scroll(self._line)
        except Exception:
            pass

    # ------------------------------------------------------------ keys
    def onAction(self, action):
        aid = action.getId()
        if aid == 203:
            return
        self.touched = time.time()
        if self.reader:
            if aid in SS.A_BACK or aid in (SS.A_LEFT, SS.A_RIGHT):
                self._close_story()
            elif aid == SS.A_DOWN:
                self._scroll(self._line + 4)
            elif aid == SS.A_UP:
                self._scroll(self._line - 4)
            elif aid in (5, 6):         # page up / page down
                self._scroll(self._line + (12 if aid == 6 else -12))
            return
        if aid in (SS.A_LEFT, SS.A_RIGHT, SS.A_UP, SS.A_DOWN):
            self.go_browse()
            return
        if aid == SS.A_SELECT:
            self._ok_at = time.time()
            self.go_browse()
            self._open_story()
            return
        if self.browse and aid not in SS.A_BACK:
            return
        self.stop()

    def onClick(self, control_id):
        # OK arrives as a key and as a click: the key already opened the story
        if self.reader or time.time() - getattr(self, '_ok_at', 0.0) < 1.0:
            return
        self.go_browse()
        self._open_story()


def run(style, browse=False):
    path = _assets()
    if style == 'news':
        window = News('dexhub_ss_news.xml', path, 'Default', '1080i', browse=browse)
    else:
        window = YourList('dexhub_ss_yourlist.xml', path, 'Default', '1080i', browse=browse)
    monitor = SS._Monitor(window)
    try:
        window.doModal()
    finally:
        window.stop()
        del monitor
        del window
