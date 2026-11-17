"""Missing Home artwork: clean each candidate, preserve real source artwork."""
import re
from urllib.parse import parse_qsl, urlsplit


def clean(value):
    value = str(value or '').strip()
    low = value.lower()
    if low in ('', 'none', 'null', 'undefined'):
        return ''
    if 'plugin.video.dexhub' in low and ('/resources/media/' in low or 'fanart.jpg' in low or 'icon.png' in low):
        return ''
    if low.startswith('default') and low.endswith('.png') and '/' not in low:
        return ''
    return value


def first(*values):
    return next((candidate for value in values if (candidate := clean(value))), '')


def display_art(art):
    """Only a render-time fallback; never cache a backdrop as a real poster."""
    result = dict(art or {})
    for key in ('poster', 'thumb', 'landscape', 'fanart'):
        result[key] = clean(result.get(key))
    if not result.get('poster'):
        result['poster'] = first(result.get('landscape'), result.get('fanart'), result.get('thumb'))
    if not result.get('landscape'):
        result['landscape'] = first(result.get('fanart'), result.get('thumb'), result.get('poster'))
    if not result.get('thumb'):
        result['thumb'] = result.get('poster') or result.get('landscape') or ''
    return result


def repair_missing_poster(tile):
    """Fill absent art by exact ID; do not replace source text or guess by name."""
    from .poster_roles import portrait
    tile['poster'] = portrait(clean(tile.get('poster')))
    if tile['poster']:
        return False
    from . import profile_scope
    profile_scope.require_current()
    params = dict(parse_qsl(urlsplit(str(tile.get('path') or '')).query))
    canonical = str(params.get('canonical_id') or '')
    imdb = str(tile.get('imdb_id') or '')
    tmdb = str(tile.get('tmdb_id') or '')
    if not re.fullmatch(r'tt[0-9]+', imdb):
        match = re.fullmatch(r'(tt[0-9]+)(?::[0-9]+:[0-9]+)?', canonical)
        imdb = match.group(1) if match else ''
    if not re.fullmatch(r'[0-9]+', tmdb):
        match = re.fullmatch(r'tmdb:([0-9]+)(?::[0-9]+:[0-9]+)?', canonical)
        tmdb = match.group(1) if match else ''
    if not imdb and not tmdb:
        return False
    from .tmdb_direct import meta_for, meta_for_cached_only
    query = dict(tmdb_id=tmdb, imdb_id=imdb,
                 media_type='series' if tile.get('media_type') in ('series', 'episode', 'anime') else 'movie',
                 title='', year='')
    found = meta_for_cached_only(**query) or meta_for(**query) or {}
    profile_scope.require_current()
    poster = clean(found.get('poster'))
    if not poster:
        return False
    tile['poster'] = poster
    if not clean(tile.get('fanart')):
        tile['fanart'] = first(found.get('fanart'), found.get('background'))
    return True
