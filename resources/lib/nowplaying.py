# -*- coding: utf-8 -*-
"""5.10.302: what is playing, kept for the Home while the video runs behind.

The source window clears its dexhub.source.* artwork when it closes, and the
player's own item often carries no clear logo, so the Home had nothing to
show for the title playing behind it. These three Home properties belong to
the playback alone: written when it starts, cleared when it stops, and the
logo is looked up on TMDb (cached) when the add-on had none.
"""
import threading

import xbmcgui

LOGO = 'dexhub.np.logo'
TITLE = 'dexhub.np.title'
KEY = 'dexhub.np.key'
EPISODE = 'dexhub.np.episode'     # v5.10.539: Kodi 22's minimal item has no season or episode

_LOCK = threading.Lock()


def _home():
    return xbmcgui.Window(10000)


def _key(ctx):
    ctx = ctx or {}
    parts = [str(ctx.get(k) or '').strip() for k in
             ('media_type', 'canonical_id', 'tmdb_id', 'imdb_id', 'season', 'episode')]
    return '|'.join(parts)


def _series(ctx):
    ctx = ctx or {}
    return bool(ctx.get('season') or ctx.get('episode') or
                str(ctx.get('media_type') or '').lower() in ('series', 'tv', 'episode', 'anime'))


def _show(ctx):
    """The series' own name. An id stands in for it on some rows: no name."""
    ctx = ctx or {}
    name = str(ctx.get('show_title') or ctx.get('tvshowtitle') or '').strip()
    if name in (str(ctx.get('canonical_id') or ''), str(ctx.get('imdb_id') or '')):
        return ''
    if name.lower().startswith('tmdb:') or (name.startswith('tt') and name[2:3].isdigit() and ' ' not in name):
        return ''
    return name


def _title(ctx):
    """v5.10.540: a series' name, never its episode's: an episode's ctx can
    come with show_title empty and title the episode (Kodi 22's minimal item
    has no name of its own to fall back on). Looked up when missing."""
    ctx = ctx or {}
    if _series(ctx):
        return _show(ctx)
    return str(ctx.get('title') or '').strip()


def _episode(ctx):
    ctx = ctx or {}
    try:
        return 'S%dE%d' % (int(ctx.get('season')), int(ctx.get('episode')))
    except (TypeError, ValueError):
        return ''


def _logo_from(ctx):
    ctx = ctx or {}
    for key in ('clearlogo', 'logo', 'tvshow.clearlogo'):
        value = str(ctx.get(key) or '').strip()
        if value.startswith(('http', 'special:', '/', 'image:')):
            return value
    return ''


def publish(ctx, lookup=True):
    """Remember the playing title; look its logo up when it came without."""
    try:
        home = _home()
        key = _key(ctx)
        logo = _logo_from(ctx)
        if not logo and key and home.getProperty(KEY) == key:
            logo = home.getProperty(LOGO)
        same, kept = home.getProperty(KEY) == key, home.getProperty(TITLE)
        home.setProperty(KEY, key)
        home.setProperty(EPISODE, _episode(ctx))
        home.setProperty(LOGO, logo)
        title = _title(ctx)
        if not title and key and same:
            title = kept
        home.setProperty(TITLE, title)
        if (not logo or not title) and lookup:
            _lookup_async(dict(ctx or {}), key)
    except Exception:
        pass


def clear():
    try:
        home = _home()
        for prop in (LOGO, TITLE, KEY, EPISODE):
            home.clearProperty(prop)
    except Exception:
        pass


def from_session(ctx):
    """Called by the service once the video is on screen."""
    ctx = dict(ctx or {})
    if not (ctx.get('tmdb_id') or ctx.get('imdb_id') or ctx.get('title')):
        return
    if not _logo_from(ctx):
        try:
            home = _home()
            for prop in ('dexhub.source.clearlogo', 'ArcticFuse.NowPlaying.Logo'):
                value = home.getProperty(prop)
                if value:
                    ctx['clearlogo'] = value
                    break
        except Exception:
            pass
    publish(ctx)


def _lookup_async(ctx, key):
    if not (ctx.get('tmdb_id') or ctx.get('imdb_id')):
        return
    worker = threading.Thread(target=_lookup, args=(ctx, key), name='dexhub-np-logo')
    worker.daemon = True
    worker.start()


def _lookup(ctx, key):
    with _LOCK:
        try:
            from . import tmdb_direct
            media = 'tv' if (ctx.get('season') or ctx.get('episode') or
                             str(ctx.get('media_type') or '').lower() in ('series', 'tv', 'episode', 'anime')) else 'movie'
            imdb = str(ctx.get('show_imdb_id') or ctx.get('imdb_id') or '')
            if media == 'tv' and ':' in imdb:
                imdb = imdb.split(':', 1)[0]
            art = tmdb_direct.art_for(tmdb_id=str(ctx.get('show_tmdb_id') or ctx.get('tmdb_id') or ''),
                                      imdb_id=imdb, media_type=media) or {}
            logo = art.get('clearlogo') or ''
            home = _home()
            if logo and home.getProperty(KEY) == key and not home.getProperty(LOGO):
                home.setProperty(LOGO, logo)
            if media == 'tv' and not home.getProperty(TITLE):
                meta = tmdb_direct.meta_for(tmdb_id=str(ctx.get('show_tmdb_id') or ctx.get('tmdb_id') or ''),
                                            imdb_id=imdb, media_type=media) or {}
                name = str(meta.get('name') or meta.get('title') or '').strip()
                if name and home.getProperty(KEY) == key:
                    home.setProperty(TITLE, name)
        except Exception:
            pass
