# -*- coding: utf-8 -*-
"""Discover as a tab of skin.dexhub's Home (v5.10.466).

Hovering Discover on the menu bar shows its sections on the Home itself,
like Movies and Series: rows the Discover page reads (Trakt when linked,
else TMDb), each loaded on its own (skin_discover, homeui.rows.capture_tiles)
and kept by discover_feed's cache. OK on the bar's Discover opens the page.
"""
from urllib.parse import urlencode

# (section, Arabic title, English title, shape)
SECTIONS = (
    ('trending', 'الرائج الآن', 'Trending now', 'poster'),
    ('foryou', 'مختارة لك', 'Picked for you', 'landscape'),
    ('top10', 'الأكثر مشاهدة هذا الأسبوع', 'Most watched this week', 'ranked'),
    ('anticipated', 'الأكثر انتظاراً', 'Most anticipated', 'tall'),
    ('toprated', 'الأعلى تقييماً', 'Top rated of all time', 'landscape'),
    ('short', 'وقتك قصير · أقل من ساعة ونص', 'Short on time · under 95 min', 'poster'),
    ('feel', 'مزاج رايق', 'Feel good', 'poster'),
    ('tense', 'إثارة وتشويق', 'Edge of your seat', 'poster'),
    ('mind', 'يحرك تفكيرك', 'Mind benders', 'poster'),
    ('calendar', 'صدر مؤخراً وقادم قريباً', 'Out lately and coming soon', 'landscape'),
)


def _t(ar, en):
    try:
        from ..i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _source(section):
    from .. import discover_feed as F
    if section in ('trending', 'top10', 'anticipated', 'foryou'):
        return 'Trakt' if F.trakt_on() else 'TMDb'
    return 'TMDb' if section != 'calendar' else ''


def skin_rows():
    """The Discover tab's rows (homeui Row objects), nothing read yet."""
    from ..homeui import rows as R
    out = []
    for section, ar, en, shape in SECTIONS:
        out.append(R.Row('disc:%s' % section, _t(ar, en), shape=shape, kind='catalog',
                         subtitle=_source(section),
                         meta={'params': {'action': 'skin_discover', 'section': section}}))
    return out


def _rows_of(section):
    from .. import discover_feed as F
    if section == 'trending':
        return F.trending()[0]
    if section == 'foryou':
        return F.for_you()[0]
    if section == 'top10':
        return F.most_watched()[0]
    if section == 'anticipated':
        return F.anticipated()[0]
    if section == 'toprated':
        return F.top_rated()[0]
    if section == 'short':
        return F.by_time('short', 'all', 'popular', F.empty_filter(), (), limit=20)
    if section in F.MOODS:
        return F.by_mood(section, 'all', 'popular', F.empty_filter(), (), limit=20)
    if section == 'calendar':
        return F.tickets('all', limit=20)
    return []


def _tile(row):
    media = 'series' if row.get('kind') == 'series' else 'movie'
    tmdb = str(row.get('tmdb') or '')
    title = row.get('title') or ''
    route = [('action', 'item_open'), ('media_type', media), ('canonical_id', 'tmdb:%s' % tmdb if tmdb else ''),
             ('title', title), ('tmdb_id', tmdb), ('imdb_id', row.get('imdb') or '')]
    hint = ''
    if row.get('code') or row.get('sub'):
        hint = '  ·  '.join(p for p in (row.get('code'), row.get('sub')) if p)
    return {
        'kind': 'work', 'media_type': media, 'title': title, 'label': title,
        'year': str(row.get('year') or ''), 'rating': float(row.get('rating') or 0),
        'plot': row.get('plot') or '', 'genres': list(row.get('genres') or [])[:3],
        'duration': int(row.get('runtime') or 0) * 60, 'aired': (row.get('date') or '')[:10],
        'poster': row.get('poster') or '', 'fanart': row.get('fanart') or '',
        'landscape': row.get('fanart') or '', 'clearlogo': '',
        'tmdb_id': tmdb, 'imdb_id': row.get('imdb') or '', 'hint': hint,
        'path': 'plugin://plugin.video.dexhub/?' + urlencode([(k, v) for k, v in route if v]),
        'folder': media == 'series', 'shape': 'poster',
    }


def tiles(params):
    """A section's titles as Home tiles (capture_tiles' skin_discover)."""
    rows = _rows_of(params.get('section') or '') or []
    return [_tile(row) for row in rows if row and row.get('tmdb') and row.get('poster')]
