# -*- coding: utf-8 -*-
"""Lightweight BetterPosters (btttr.cc) URL integration.

The module deliberately performs no HTTP requests.  A configured poster URL
is derived from an IMDb id in memory and Kodi remains responsible for its
normal image download/cache.  This keeps catalog rendering on the v5.8.4
cache-only path and avoids bringing back per-card metadata work.
"""

from urllib.parse import quote


BASE_URL = 'https://btttr.cc'
DEFAULT_TEMPLATE = BASE_URL + '/poster/imdb/poster-default/{imdb_id}.jpg?lang=ar&rs=IM'
DEFAULT_FALLBACK_TEMPLATE = BASE_URL + '/poster/imdb/poster-default/{imdb_id}.jpg?rs=IM'

LANGUAGES = (
    ('ar', 'العربية'), ('', 'English'), ('es', 'Español'), ('fr', 'Français'),
    ('de', 'Deutsch'), ('pt-BR', 'Português (Brasil)'),
    ('pt-PT', 'Português (Portugal)'), ('it', 'Italiano'),
    ('nl', 'Nederlands'), ('pl', 'Polski'), ('ru', 'Русский'),
    ('tr', 'Türkçe'), ('ja', '日本語'), ('ko', '한국어'), ('zh', '中文'),
    ('hi', 'हिन्दी'), ('sv', 'Svenska'), ('cs', 'Čeština'),
)

RATING_SOURCES = (
    ('', 'Average'), ('IM', 'IMDb'), ('TM', 'TMDb'),
    ('RT', 'Rotten Tomatoes'), ('MC', 'Metacritic'), ('TR', 'Trakt'),
    ('LB', 'Letterboxd'), ('RE', 'Roger Ebert'),
)


def _bool(value, default=False):
    if value is None or value == '':
        return bool(default)
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _read(addon, key, default=''):
    try:
        value = addon.getSetting(key)
    except Exception:
        value = ''
    return str(value if value not in (None, '') else default).strip()


_CONFIG_CACHE = {}


def _make_config(values):
    data = dict(values)
    return {
        'enabled': _bool(data.get('enabled'), True),
        'language': '' if data.get('language', 'ar') == 'English' else data.get('language', 'ar'),
        'trend': _bool(data.get('trend'), True),
        'quality': _bool(data.get('quality'), False),
        'genre': _bool(data.get('genre'), True),
        'rating': _bool(data.get('rating'), True),
        'rating_source': '' if data.get('rating_source', 'IM') == 'Average' else data.get('rating_source', 'IM'),
        'age': _bool(data.get('age'), False),
        'template': data.get('template', ''),
        'fallback_template': data.get('fallback_template', ''),
        'catalogs_enabled': _bool(data.get('catalogs_enabled'), True),
    }


def config(addon):
    try:
        addon_key = str(addon.getAddonInfo('id') or id(addon))
    except Exception:
        addon_key = str(id(addon))
    cached = _CONFIG_CACHE.get(addon_key)
    if cached is not None:
        return cached
    keys = (
        ('enabled', 'betterposters_enabled', 'true'),
        ('language', 'betterposters_language', 'ar'),
        ('trend', 'betterposters_trend_tags', 'true'),
        ('quality', 'betterposters_quality_tags', 'false'),
        ('genre', 'betterposters_genre', 'true'),
        ('rating', 'betterposters_rating', 'true'),
        ('rating_source', 'betterposters_rating_source', 'IM'),
        ('age', 'betterposters_age_rating', 'false'),
        ('template', 'betterposters_url_template', ''),
        ('fallback_template', 'betterposters_fallback_template', ''),
        ('catalogs_enabled', 'betterposters_catalogs_enabled', 'true'),
    )
    values = tuple((name, _read(addon, setting, default))
                   for name, setting, default in keys)
    result = _make_config(values)
    _CONFIG_CACHE[addon_key] = result
    return result


def invalidate():
    _CONFIG_CACHE.clear()


def _poster_path(cfg):
    if cfg.get('genre') and cfg.get('rating'):
        suffix = ''
    elif not cfg.get('genre') and cfg.get('rating'):
        suffix = 'r'
    elif cfg.get('genre'):
        suffix = 'g'
    else:
        suffix = 'n'
    if cfg.get('quality'):
        suffix += 'q'
    if cfg.get('age'):
        suffix += 'a'
    return 'poster' + (('-' + suffix) if suffix else '')


def build_url(imdb_id, cfg):
    imdb_id = str(imdb_id or '').strip()
    if not imdb_id.startswith('tt') or not imdb_id[2:].isdigit():
        return ''

    custom = str(cfg.get('template') or '').strip()
    if custom and '{imdb_id}' in custom and custom.lower().startswith('https://'):
        return custom.replace('{imdb_id}', quote(imdb_id, safe=''))

    url = '%s/%s/imdb/poster-default/%s.jpg' % (
        BASE_URL, _poster_path(cfg), quote(imdb_id, safe=''))
    query = []
    if not cfg.get('trend'):
        query.append(('tag', 'none'))
    language = str(cfg.get('language') or '').strip()
    if language:
        query.append(('lang', language))
    rating_source = str(cfg.get('rating_source') or '').strip().upper()
    if cfg.get('rating') and rating_source:
        query.append(('rs', rating_source))
    if query:
        url += '?' + '&'.join('%s=%s' % (quote(k, safe=''), quote(v, safe=''))
                              for k, v in query)
    return url


def fallback_url(imdb_id, cfg):
    template = str(cfg.get('fallback_template') or '').strip()
    imdb_id = str(imdb_id or '').strip()
    if (template and '{imdb_id}' in template and
            template.lower().startswith('https://') and
            imdb_id.startswith('tt') and imdb_id[2:].isdigit()):
        return template.replace('{imdb_id}', quote(imdb_id, safe=''))
    return ''


def apply(art, imdb_id, media_type, addon):
    """Put BetterPosters in the primary poster slot without network work.

    The source poster stays in the thumb/icon family.  Besides keeping small
    landscape UI elements clean, this preserves an immediate native fallback
    for skins that choose thumb when poster is unavailable.
    """
    if not isinstance(art, dict):
        return art
    if str(media_type or '').lower() not in (
            'movie', 'series', 'tv', 'show', 'tvshow', 'anime'):
        return art
    cfg = config(addon)
    if not cfg.get('enabled'):
        return art
    url = build_url(imdb_id, cfg)
    if not url:
        return art
    out = dict(art)
    # Preserve the native/cached poster for latency-sensitive screens. Kodi,
    # not this module, downloads the generated BTTR URL; source discovery can
    # therefore restore this value and avoid competing image I/O.
    original = (out.get('dexhub.betterposters.original') or
                out.get('poster') or out.get('thumb') or out.get('icon') or '')
    if original and not is_managed_url(original):
        out['dexhub.betterposters.original'] = original
    out['poster'] = url
    out['tvshow.poster'] = url
    out['season.poster'] = url
    # Expose the explicit non-localized URL for custom skins and diagnostics.
    fallback = fallback_url(imdb_id, cfg)
    if fallback:
        out['dexhub.betterposters.fallback'] = fallback
    return out


def is_managed_url(url):
    """True for a poster URL generated by the built-in BTTR integration."""
    return str(url or '').strip().lower().startswith('https://btttr.cc/')
