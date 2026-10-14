# -*- coding: utf-8 -*-
"""Built-in BetterPosters discovery rows for the Nuvio Movies/TV surfaces.

These are intentionally independent from the user's Nuvio snapshot: they are
stable Dex Hub rows, then the exact Nuvio rows follow in the user's own order.
Catalog content is fetched from Cinemeta because it is IMDb-native, allowing
Dex Hub's existing BetterPosters URL pass to decorate every poster without an
extra TMDb->IMDb lookup per card.
"""
import time
from datetime import datetime

from . import better_posters
from .dexhub.client import fetch_catalog

# v5.10.104: when Cinemeta does not answer with a catalog (the user's
# kodi.log: every BetterPosters row failed with "Expecting value", some after
# 6 seconds, on every Home build), the rows are left empty for a while
# instead of each asking again. The pause is shared by every interpreter
# (a Home window property) and logged once.
_PAUSE = 30 * 60
_PAUSE_PROP = 'dexhub.betterposters.paused_until'


def _home():
    import xbmcgui
    return xbmcgui.Window(10000)


def _paused():
    try:
        return time.time() < float(_home().getProperty(_PAUSE_PROP) or 0)
    except Exception:
        return False


def _pause(reason, log):
    try:
        first = not _home().getProperty(_PAUSE_PROP)
        _home().setProperty(_PAUSE_PROP, str(time.time() + _PAUSE))
    except Exception:
        first = True
    if first:
        log('[DexHub] BetterPosters catalogs paused for %d minutes: Cinemeta did not '
            'answer with a catalog (%s)' % (_PAUSE // 60, reason))


def _resume():
    try:
        if _home().getProperty(_PAUSE_PROP):
            _home().clearProperty(_PAUSE_PROP)
    except Exception:
        pass

_CINEMETA = {
    'id': 'dexhub_betterposters_cinemeta',
    'name': 'BetterPosters',
    'base_url': 'https://v3-cinemeta.strem.io',
    'manifest_url': 'https://v3-cinemeta.strem.io/manifest.json',
    'manifest': {'id': 'com.linvo.cinemeta', 'name': 'Cinemeta'},
}

# Stable rows per media page.  Cinemeta's IDs are IMDb-native.
_CATALOGS = {
    'movie': (
        ('popular', 'top', None, 'bp_popular_movie'),
        ('latest', 'year', 'current_year', 'bp_latest_movie'),
        ('toprated', 'imdbRating', None, 'bp_top_movie'),
    ),
    'series': (
        ('popular', 'top', None, 'bp_popular_series'),
        ('latest', 'year', 'current_year', 'bp_latest_series'),
        ('toprated', 'imdbRating', None, 'bp_top_series'),
    ),
}

_LABELS = {
    'ar': {'popular': 'الأكثر شعبية', 'latest': 'أحدث الإصدارات', 'toprated': 'الأعلى تقييماً'},
    'en': {'popular': 'Popular', 'latest': 'Latest', 'toprated': 'Top Rated'},
    'es': {'popular': 'Populares', 'latest': 'Novedades', 'toprated': 'Mejor valorados'},
    'fr': {'popular': 'Populaires', 'latest': 'Nouveautés', 'toprated': 'Mieux notés'},
    'de': {'popular': 'Beliebt', 'latest': 'Neu', 'toprated': 'Bestbewertet'},
    'it': {'popular': 'Popolari', 'latest': 'Novità', 'toprated': 'Più votati'},
    'pt': {'popular': 'Populares', 'latest': 'Novidades', 'toprated': 'Mais bem avaliados'},
    'nl': {'popular': 'Populair', 'latest': 'Nieuw', 'toprated': 'Best beoordeeld'},
    'pl': {'popular': 'Popularne', 'latest': 'Najnowsze', 'toprated': 'Najwyżej oceniane'},
    'ru': {'popular': 'Популярное', 'latest': 'Новинки', 'toprated': 'Лучшие оценки'},
    'tr': {'popular': 'Popüler', 'latest': 'Yeni', 'toprated': 'En yüksek puanlı'},
    'ja': {'popular': '人気', 'latest': '新着', 'toprated': '高評価'},
    'ko': {'popular': '인기', 'latest': '최신', 'toprated': '최고 평점'},
    'zh': {'popular': '热门', 'latest': '最新', 'toprated': '高分'},
    'hi': {'popular': 'लोकप्रिय', 'latest': 'नवीनतम', 'toprated': 'शीर्ष रेटेड'},
    'sv': {'popular': 'Populärt', 'latest': 'Senaste', 'toprated': 'Högst betyg'},
    'cs': {'popular': 'Populární', 'latest': 'Nejnovější', 'toprated': 'Nejlépe hodnocené'},
}


def _lang(addon):
    raw = str((better_posters.config(addon) or {}).get('language') or '').strip()
    if not raw or raw == 'English':
        return 'en'
    return raw.replace('_', '-').split('-', 1)[0].lower()


def _label(key, addon):
    lang = _lang(addon)
    return (_LABELS.get(lang) or _LABELS['en']).get(key, key)


def enabled(addon):
    cfg = better_posters.config(addon)
    return bool(cfg.get('enabled') and cfg.get('catalogs_enabled', True))


def tabs(media_type, addon, build_url, art_for_key):
    media_type = 'series' if str(media_type).lower() in ('series', 'tv', 'tvshow', 'show') else 'movie'
    if not enabled(addon):
        return []
    out = []
    for key, catalog_id, extra_kind, art_key in _CATALOGS[media_type]:
        label = _label(key, addon)
        out.append({
            'kind': 'betterposters',
            'key': 'betterposters:%s:%s' % (media_type, key),
            'label': label,
            'native_label': label,
            'media_type': media_type,
            'path': build_url(action='betterposters_catalog', media_type=media_type,
                              catalog_key=key, page='0'),
            'is_folder': True,
            'art': art_for_key(art_key),
            'fanart': (art_for_key(art_key) or {}).get('fanart') or '',
            'info': {'title': label,
                     'plot': 'BetterPosters • %s' % label,
                     'mediatype': 'video'},
            'properties': {
                'dexhub.tile_shape': 'landscape',
                'dexhub.betterposters_catalog': '1',
                'dexhub.nuvio_home_media': media_type,
            },
        })
    return out


def render(catalog_key, media_type, page, addon, plugin_api):
    media_type = 'series' if str(media_type).lower() in ('series', 'tv', 'tvshow', 'show') else 'movie'
    defs = {row[0]: row for row in _CATALOGS.get(media_type, ())}
    row = defs.get(str(catalog_key or ''))
    if not row:
        return plugin_api.nuvio_home_media(media_type)
    key, catalog_id, extra_kind, _art_key = row
    try:
        page_num = max(0, int(page or 0))
    except Exception:
        page_num = 0
    page_size = max(12, min(40, int(plugin_api._listing_page_size())))
    extra = {'skip': str(page_num * page_size)} if page_num else {}
    if extra_kind == 'current_year':
        extra['genre'] = str(datetime.now().year)

    def _log(message):
        plugin_api.xbmc.log(message, plugin_api.xbmc.LOGWARNING)

    data = {}
    if not _paused():
        try:
            data = fetch_catalog(_CINEMETA, media_type, catalog_id, extra=extra,
                                 timeout_override=min(4, plugin_api.fast_timeout('catalog')),
                                 retry=False) or {}
            if isinstance(data, dict) and isinstance(data.get('metas'), list):
                _resume()
            else:
                _pause('no catalog in the reply', _log)
                data = {}
        except Exception as exc:
            _pause('%s: %s' % (key, type(exc).__name__), _log)
            data = {}
    metas = [m for m in (data.get('metas') or []) if isinstance(m, dict)]
    rows = []
    for meta in metas[:page_size]:
        mid = str(meta.get('id') or '').strip()
        if not mid:
            continue
        rows.append((_CINEMETA, {'id': catalog_id, 'name': _label(key, addon), 'type': media_type},
                     media_type, mid, meta))
    if rows:
        plugin_api._hub_render_media_rows(rows,
            bucket='movies' if media_type == 'movie' else 'series',
            page_num=page_num, has_more=False)
    if len(metas) >= page_size and not plugin_api._pagination_hidden():
        plugin_api.add_item(plugin_api.tr('المزيد'),
            plugin_api.build_url(action='betterposters_catalog', media_type=media_type,
                                 catalog_key=key, page=str(page_num + 1)),
            art=plugin_api.root_art('catalogs'), info={'title': plugin_api.tr('المزيد')})
    return plugin_api.end_dir(content=plugin_api._directory_content_for_type(media_type), cache=True)
