# -*- coding: utf-8 -*-
"""Work-level identity for subtitle lookups and player bridges.

v5.10.94. Native library rows (Silo, Plex, Emby/Jellyfin) carry two sets of
ids for an episode: the episode's own ``imdb_id`` / ``tmdb_id`` / ``tvdb_id``
(Silo and TMDb both assign ids to individual episodes) and the series ids in
``show_imdb_id`` / ``show_tmdb_id`` / ``show_tvdb_id``. Every subtitle
contract Dex Hub talks to (DexSubtitles, OpenSubtitles-style Stremio addons,
service.subtitles.dexworld, TMDb Helper's tvshow.* unique ids) identifies an
episode as ``<series id>:<season>:<episode>``. Sending the episode's own id
in that slot produces ``tt<episode>:S:E``, which matches nothing, and a
TMDb episode id read as a series id resolves to a different work entirely.

This module is the one place that decides which ids describe the *work*.
"""
from __future__ import absolute_import

import re

EPISODE_TYPES = ('series', 'episode', 'show', 'tv', 'anime', 'season')

_IMDB_RE = re.compile(r'\btt\d{5,10}\b', re.I)
# v5.10.242: S01E05, S1 E5, S01.E05, 1x05 in a title or a file name
_SXE_RE = re.compile(r'(?<![a-z0-9])s(\d{1,3})[ ._-]?e(\d{1,4})(?!\d)', re.I)
_NXN_RE = re.compile(r'(?<!\d)(\d{1,2})x(\d{2,3})(?![\dp])', re.I)


_VIDEO_SXE_RE = re.compile(r'^(?:tt\d{5,10}|(?:tmdb|tvdb|kitsu|mal|anilist|imdb):[\w.]+)[:_](\d{1,3})[:_](\d{1,4})$', re.I)


def episode_from_text(*texts):
    """(season, episode) found in any of the texts, or (None, None)."""
    for text in texts:
        text = str(text or '')
        if not text:
            continue
        for rx in (_SXE_RE, _NXN_RE):
            m = rx.search(text)
            if m:
                season, episode = int(m.group(1)), int(m.group(2))
                if episode > 0:
                    return season, episode
    return None, None


def as_episode_ctx(ctx, *texts):
    """The context as the subtitle add-ons need it: an episode is always
    type series with its season and episode. An item handed over as a movie
    (or with no type) whose numbers are known, or whose title or file name
    says S01E05, becomes an episode. Returns a new dict; the input is kept."""
    ctx = dict(ctx or {})
    media_type = str(ctx.get('media_type') or ctx.get('mediatype') or '').strip().lower()
    try:
        season, episode = int(ctx.get('season') or 0), int(ctx.get('episode') or 0)
    except (TypeError, ValueError):
        season = episode = 0
    if episode <= 0:
        # v5.10.247: the episode's own play id says it ("tt26545992:1:2",
        # "tmdb:95350:1:2"); a native server play (Emby, Plex, Silo) can
        # carry it without separate season and episode fields
        for key in ('video_id', 'canonical_video_id'):
            m = _VIDEO_SXE_RE.search(str(ctx.get(key) or ''))
            if m and int(m.group(2)) > 0:
                season, episode = int(m.group(1)), int(m.group(2))
                ctx['season'], ctx['episode'] = season, episode
                break
    if episode <= 0:
        found = episode_from_text(*(texts + (ctx.get('title'), ctx.get('name'), ctx.get('filename'),
                                            ctx.get('stream_title'), ctx.get('stream_name'))))
        if found[1]:
            season, episode = found
            ctx['season'], ctx['episode'] = season, episode
    if media_type in EPISODE_TYPES or episode > 0:
        ctx['media_type'] = 'series'
    elif not media_type:
        ctx['media_type'] = 'movie'
    return ctx


def is_episode_ctx(ctx):
    ctx = ctx or {}
    media_type = str(ctx.get('media_type') or ctx.get('mediatype') or '').strip().lower()
    if media_type in EPISODE_TYPES:
        return True
    if media_type in ('movie', 'film'):
        return False
    # Unknown media type: an episode number is the next best signal.
    try:
        return int(ctx.get('season') or 0) > 0 and int(ctx.get('episode') or 0) > 0
    except Exception:
        return False


def clean_imdb(value):
    text = str(value or '').strip().lower()
    if text.startswith('imdb:'):
        text = text.split(':', 1)[1]
    m = _IMDB_RE.search(text)
    return m.group(0).lower() if m else ''


def clean_numeric(value, prefix=''):
    text = str(value or '').strip().lower()
    if prefix and text.startswith(prefix + ':'):
        text = text.split(':', 1)[1]
    text = text.split(':', 1)[0]
    return text if text.isdigit() else ''


def work_ids(ctx):
    """Return {'imdb_id','tmdb_id','tvdb_id'} for the work the subtitle or
    metadata lookup should be keyed on.

    Movies: the row's own ids. Episodes: ``show_*`` ids first, then the
    plain ids only when no series id of that kind exists (a plain id on an
    episode context from a Stremio catalog is already the series id, so the
    fallback keeps those paths unchanged).
    """
    ctx = ctx or {}
    episode = is_episode_ctx(ctx)
    out = {}
    for kind, cleaner in (('imdb', clean_imdb), ('tmdb', None), ('tvdb', None)):
        plain = ctx.get('%s_id' % kind)
        show = ctx.get('show_%s_id' % kind) if episode else ''
        if cleaner:
            value = cleaner(show) or cleaner(plain)
        else:
            value = clean_numeric(show, kind) or clean_numeric(plain, kind)
        out['%s_id' % kind] = value
    if episode and not out['imdb_id']:
        # canonical_id / video_id on episode contexts start with the series
        # id ("tt123:1:2"), so their imdb part is safe to use.
        for key in ('canonical_id', 'video_id'):
            found = clean_imdb(ctx.get(key))
            if found:
                out['imdb_id'] = found
                break
    return out


def episode_ids_differ(ctx):
    """True when the row's own ids and the series ids disagree (the Silo /
    native-server case). Used only for logging."""
    ctx = ctx or {}
    if not is_episode_ctx(ctx):
        return False
    for kind in ('imdb', 'tmdb', 'tvdb'):
        plain = str(ctx.get('%s_id' % kind) or '').strip().lower()
        show = str(ctx.get('show_%s_id' % kind) or '').strip().lower()
        if plain and show and plain != show:
            return True
    return False
