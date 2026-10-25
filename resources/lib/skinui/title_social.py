# -*- coding: utf-8 -*-
"""v5.10.406: the title page's Reviews and Pictures rows (skin.dexhub).

Both lived behind More (a list of names, Nuvio's picker); they are rows of
the page now, between Cast and More like this:

* Reviews: "Write your review" first when Trakt is linked (it is posted to
  Trakt under the user's name), then Trakt's and TMDb's reviews as cards
  (author, source, rating, likes, an excerpt; a spoiler stays hidden until
  it is opened). OK reads one whole.
* Pictures: TMDb's backdrops and logos as cards; OK puts one on the title
  (art_choice, kept per title), "Original pictures" takes the choice back.
"""
import time
from urllib.parse import urlencode

import xbmc
import xbmcgui

from . import common as C

REV_REVIEWS = 'dhs.tp.rev.reviews'
REV_ART = 'dhs.tp.rev.artwork'
MIN_WORDS = 5           # Trakt refuses a shorter comment


def _tr(text):
    try:
        from ..i18n import tr
        return tr(text)
    except Exception:
        return text


def _target(action, q, **extra):
    return C.BASE + '?' + urlencode([('action', action)] + sorted(extra.items())) + '&' + urlencode(sorted(q.items()))


def _card(label, label2, target, props, art=None):
    """One card. Its path runs the same target (skin_go answers the resolve):
    Kodi 22 on CoreELEC played the card's empty path instead of the list's
    onclick ("error opening []"), so a picked picture never applied."""
    props = dict(props, **{'dhs.click': 'run', 'dhs.target': target})
    return {'label': label, 'label2': label2, 'path': C.url('skin_go', do='run', u=target), 'folder': False,
            'art': art or {}, 'props': props}


def _work(title):
    media = 'series' if title.series else 'movie'
    tmdb = str(title.tmdb_id or title.hero.get('tmdb_id') or '')
    if not tmdb.isdigit():
        try:
            tmdb = str(title._resolve_tmdb_id() or '')
        except Exception:
            tmdb = ''
    return media, title._imdb(), tmdb


def _stars(rating):
    try:
        five = max(0, min(5, int(round(float(rating) / 2.0))))
    except (TypeError, ValueError):
        return ''
    return '★' * five + '☆' * (5 - five)


def _initial(name):
    name = str(name or '?').strip().lstrip('@')
    return (name[:1] or '?').upper()


# ------------------------------------------------------------------ ahead
def warm(title):
    """v5.10.413: the reviews and pictures read into their caches while the
    cursor rests on the card (the Home's read ahead), so the page's rows,
    which load after Cast, come from the cache when it opens."""
    from .. import title_reviews, art_choice
    media, imdb, tmdb = _work(title)
    if imdb or tmdb:
        try:
            title_reviews.reviews(media, imdb, tmdb)
        except Exception:
            pass
    if tmdb.isdigit():
        try:
            art_choice._images(tmdb, media)
        except Exception:
            pass


# ------------------------------------------------------------------ rows
def review_items(title, q):
    from .. import title_reviews, trakt
    C.set_prop('dhs.tp.h.reviews', _tr('المراجعات والتعليقات'))     # the row's heading
    media, imdb, tmdb = _work(title)
    out = []
    try:
        linked = trakt.linked()
    except Exception:
        linked = False
    if linked and (imdb or tmdb):
        out.append(_card(_tr('اكتب مراجعتك'), _tr('تنشر في Trakt باسمك'), _target('skin_review_write', q),
                         {'dhs.kind': 'write', 'dhs.initial': '+',
                          'dhs.hint': _tr('قل رأيك في العمل، تطلع مراجعتك هنا وفي Trakt للي يتابعونك')}))
    try:
        rows = title_reviews.reviews(media, imdb, tmdb)
    except Exception:
        rows = []
    for index, row in enumerate(rows):
        spoiler = bool(row.get('spoiler'))
        excerpt = _tr('فيها حرق للأحداث، اضغط تقراها') if spoiler else title_reviews._short(row.get('text') or '')
        props = {'dhs.kind': 'review', 'dhs.src': row.get('src') or '', 'dhs.initial': _initial(row.get('author')),
                 'dhs.date': row.get('date') or '', 'dhs.spoiler': '1' if spoiler else '',
                 'dhs.rating': str(row.get('rating') or ''), 'dhs.stars': _stars(row.get('rating')),
                 'dhs.likes': (_tr('%d إعجاب') % int(row['likes'])) if row.get('likes') else '',
                 'dhs.review': '1' if row.get('review') else ''}
        out.append(_card(row.get('author') or row.get('src') or '', excerpt,
                         _target('skin_review_read', q, n=str(index)), props))
    return out


def artwork_items(title, q):
    from .. import art_choice
    C.set_prop('dhs.tp.h.artwork', _tr('الصور والشعارات'))
    media, imdb, tmdb = _work(title)
    if not tmdb.isdigit():
        return []
    try:
        data = art_choice._images(tmdb, media)
    except Exception:
        return []
    mine = art_choice.chosen(tmdb, imdb, media) or {}
    try:
        from ..i18n import is_english
        lang = 'en' if is_english() else 'ar'
    except Exception:
        lang = 'ar'
    out = []
    if mine:
        out.append(_card(_tr('الصور الأصلية'), _tr('ترجع صور العمل الأصلية'),
                         _target('skin_art_set', q, field='reset'), {'dhs.kind': 'reset'}))

    def best(rows, count, langs):
        rows = [r for r in rows or [] if r.get('file_path')]
        rows.sort(key=lambda r: (str(r.get('iso_639_1') or '') not in langs,
                                 -(float(r.get('vote_average') or 0)), -(int(r.get('width') or 0))))
        return rows[:count]
    for field, group, count, langs, size, full in (
            ('clearlogo', 'logos', 8, (lang, 'en'), 'w500', 'w500'),
            ('fanart', 'backdrops', 14, ('', lang, 'en'), 'w780', 'w1280')):
        for row in best(data.get(group), count, langs):
            path = str(row.get('file_path') or '')
            if path.lower().endswith('.svg'):
                path = path[:-4] + '.png'
            url = 'https://image.tmdb.org/t/p/%s%s' % (full, path)
            code = str(row.get('iso_639_1') or '')
            line = (art_choice._lang_name(code, _tr) if field == 'clearlogo' else
                    ('%dx%d' % (int(row.get('width') or 0), int(row.get('height') or 0))))
            on = bool(mine.get(field)) and mine.get(field, '').endswith(path)
            out.append(_card(_tr('شعار') if field == 'clearlogo' else _tr('خلفية'), line,
                             _target('skin_art_set', q, field=field, url=url),
                             {'dhs.kind': field, 'dhs.on': '1' if on else ''},
                             {'thumb': 'https://image.tmdb.org/t/p/%s%s' % (size, path)}))
    return out


# ------------------------------------------------------------------ actions
def _title(params):
    from .title import HeadlessTitle, query_of
    q = query_of(params)
    title = HeadlessTitle(q)
    title.load()
    return title, q


def review_read(params, handle):
    from .. import title_reviews
    title, _q = _title(params)
    media, imdb, tmdb = _work(title)
    rows = title_reviews.reviews(media, imdb, tmdb)
    try:
        row = rows[int(params.get('n') or 0)]
    except (ValueError, IndexError):
        return None
    if row.get('spoiler') and not xbmcgui.Dialog().yesno('Dex Hub', _tr('المراجعة فيها حرق للأحداث، تبي تقراها؟')):
        return None
    head = '[B]%s[/B]  ·  %s%s' % (row.get('author') or '', row.get('src') or '',
                                   ('  ·  ' + row['date']) if row.get('date') else '')
    if row.get('rating'):
        head += '\n%s  %d/10' % (_stars(row['rating']), row['rating'])
    if row.get('likes'):
        head += '  ·  ♥ %d' % row['likes']
    xbmcgui.Dialog().textviewer(title.hero.get('title') or 'Dex Hub', head + '\n\n' + (row.get('text') or ''))
    return None


def review_write(params, handle):
    from .. import trakt, title_reviews
    title, _q = _title(params)
    media, imdb, tmdb = _work(title)
    if not trakt.linked():
        xbmcgui.Dialog().notification('Trakt', _tr('اربط Trakt عشان تكتب مراجعة'), sound=False)
        return None
    name = title.hero.get('title') or ''
    text = ''
    while True:
        kb = xbmc.Keyboard(text, _tr('مراجعتك عن %s') % name if name else _tr('مراجعتك'))
        kb.doModal()
        if not kb.isConfirmed():
            return None
        text = kb.getText().strip()
        if len(text.split()) >= MIN_WORDS:
            break
        xbmcgui.Dialog().ok('Trakt', _tr('Trakt يطلب خمس كلمات على الأقل'))
    spoiler = xbmcgui.Dialog().yesno('Trakt', _tr('فيها حرق للأحداث؟'), nolabel=_tr('لا'), yeslabel=_tr('نعم'))
    ids = {'imdb': imdb, 'tmdb': tmdb}
    try:
        trakt.post_comment(media, ids, text, spoiler=spoiler)
    except Exception as exc:
        C.log('review not posted: %s' % exc, xbmc.LOGWARNING)
        code = getattr(exc, 'code', 0)
        xbmcgui.Dialog().notification('Trakt', _tr('ما انرسلت المراجعة') + (' (HTTP %s)' % code if code else ''),
                                      xbmcgui.NOTIFICATION_ERROR, sound=False)
        return None
    try:
        from .. import tmdb_direct as T
        T._cache_set('dh-reviews:%s:%s:%s' % (media, imdb, tmdb), None, 1)    # read again with yours in it
    except Exception:
        pass
    C.set_prop(REV_REVIEWS, '%.0f' % time.time())
    xbmcgui.Dialog().notification('Trakt', _tr('انرسلت مراجعتك ✓'), sound=False)
    return None


def art_set(params, handle):
    from .. import art_choice
    title, _q = _title(params)
    media, imdb, tmdb = _work(title)
    field = params.get('field') or ''
    if field == 'reset':
        art_choice.remember(tmdb, imdb, media, forget=True)
    elif field in ('fanart', 'clearlogo') and params.get('url'):
        art_choice.remember(tmdb, imdb, media, **{field: params['url']})
    else:
        return None
    title.publish()            # the page's hero takes the new picture now
    C.set_prop(REV_ART, '%.0f' % time.time())
    return None
