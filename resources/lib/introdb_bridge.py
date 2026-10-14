# -*- coding: utf-8 -*-
"""Optional playback-metadata bridge for ``plugin.video.tidb`` 1.8+.

TheIntroDB is a standalone Kodi service.  It watches playback and reads the
current ``VideoInfoTag``; Dex Hub must not query its API or duplicate its skip
overlay.  This module only makes the final playback ListItem unambiguous.

No TheIntroDB code is imported.  When the add-on is absent or disabled every
function is a cheap no-op and playback continues normally.
"""
import re

import xbmc
import xbmcaddon


ADDON_ID = 'plugin.video.tidb'
_IMDB_RE = re.compile(r'^tt\d{5,10}$', re.I)


def _text(value):
    return str(value or '').strip()


def _positive_int(value):
    try:
        value = int(float(value))
        return value if value > 0 else 0
    except (TypeError, ValueError):
        return 0


def _season_int(value):
    """Season zero is valid for specials; None means unavailable."""
    if value in (None, ''):
        return None
    try:
        value = int(float(value))
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def _valid_tmdb(value):
    value = _text(value)
    return value if value.isdigit() and int(value) > 0 else ''


def _valid_imdb(value):
    value = _text(value)
    if value.lower().startswith('imdb:'):
        value = value.split(':', 1)[1].strip()
    if value.isdigit():
        value = 'tt%s' % value
    return value if _IMDB_RE.match(value) else ''


def is_available():
    """Return True only when TheIntroDB is installed and enabled in Kodi."""
    try:
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % ADDON_ID):
            return False
        return xbmcaddon.Addon(ADDON_ID).getAddonInfo('id') == ADDON_ID
    except Exception:
        return False


def ensure_manual_skip_buttons(force=False):
    """Keep TIDB 1.8's Intro/Recap/Credits actions user-confirmed.

    The companion add-on owns segment lookup and the overlay.  Dex Hub only
    selects its documented button mode, makes the overlay easy to catch on a
    TV remote, and leaves Preview entirely user-controlled.  Legacy boolean
    keys are also written so older TIDB settings stores cannot re-enable an
    automatic seek after upgrading to 1.8.
    """
    if not force and not is_available():
        return False
    try:
        addon = xbmcaddon.Addon(ADDON_ID)
    except Exception:
        return False
    # v5.10.221: with Dex Hub's end of episode panel on, the panel is the
    # credits button: TheIntroDB's own «Skip Credits» stays off (its modal
    # overlay also kept the panel from opening: Kodi refuses a window while
    # a modal dialog is up)
    try:
        from . import credits_panel
        credits = 'false' if credits_panel.owns_end() else 'true'
    except Exception:
        credits = 'true'
    desired = {
        'enable_intro': 'true',
        'intro_skip_mode': '0',
        'auto_skip_intro': 'false',
        'enable_recap': 'true',
        'recap_skip_mode': '0',
        'auto_skip_recap': 'false',
        'enable_credits': credits,
        'credits_skip_mode': '0',
        'auto_skip_credits': 'false',
        'overlay_duration': '30',
        'show_skip_icon': 'true',
        'show_skip_notification': 'false',
        # Pre-1.8 compatibility.  Zero meant keep the old overlay available
        # for the full detected segment.
        'button_display_seconds': '0',
    }
    changed = []
    for key, value in desired.items():
        try:
            current = _text(addon.getSetting(key)).lower()
            if current != value:
                addon.setSetting(key, value)
                changed.append(key)
        except Exception:
            continue
    try:
        if changed:
            xbmc.log('[DexHub][TheIntroDB] manual buttons configured: %s' %
                     ','.join(changed), xbmc.LOGINFO)
    except Exception:
        pass
    return True


def playback_identity(ctx, info=None):
    """Build the exact identity TheIntroDB reads from the active player.

    For episodes, TheIntroDB requires the *show* TMDb/IMDb id plus season and
    episode.  Dex contexts historically stored that show id in ``tmdb_id``;
    the explicit ``show_*`` keys added by the bridge take precedence.
    """
    ctx = dict(ctx or {})
    info = dict(info or {})
    external = ctx.get('external_ids') or {}
    if not isinstance(external, dict):
        external = {}

    season_value = ctx.get('season')
    if season_value in (None, ''):
        season_value = info.get('season')
    season = _season_int(season_value)
    episode = _positive_int(ctx.get('episode') or info.get('episode'))
    media_type = _text(
        info.get('mediatype') or ctx.get('media_type')).lower()
    is_episode = bool(
        media_type in ('episode', 'episodes') or season is not None or episode)

    if is_episode:
        tmdb_id = _valid_tmdb(
            ctx.get('show_tmdb_id') or external.get('show_tmdb_id') or
            external.get('tmdbshow') or external.get('tmdb_show') or
            ctx.get('tmdb_id') or external.get('tmdb_id') or
            external.get('tmdb'))
        imdb_id = _valid_imdb(
            ctx.get('show_imdb_id') or external.get('show_imdb_id') or
            external.get('imdbshow') or external.get('imdb_show') or
            ctx.get('imdb_id') or external.get('imdb_id') or
            external.get('imdb'))
        tvdb_id = _text(
            ctx.get('show_tvdb_id') or external.get('show_tvdb_id') or
            ctx.get('tvdb_id') or external.get('tvdb_id') or
            external.get('tvdb'))
    else:
        tmdb_id = _valid_tmdb(
            ctx.get('tmdb_id') or external.get('tmdb_id') or
            external.get('tmdb'))
        imdb_id = _valid_imdb(
            ctx.get('imdb_id') or external.get('imdb_id') or
            external.get('imdb'))
        tvdb_id = _text(
            ctx.get('tvdb_id') or external.get('tvdb_id') or
            external.get('tvdb'))

    return {
        'is_episode': is_episode,
        'media_type': 'episode' if is_episode else 'movie',
        'tmdb_id': tmdb_id,
        'imdb_id': imdb_id,
        'tvdb_id': tvdb_id,
        'season': season,
        'episode': episode,
        'title': _text(info.get('title') or ctx.get('title')),
        'show_title': _text(
            info.get('tvshowtitle') or ctx.get('show_title') or
            ctx.get('title')),
    }


def _video_tag(item):
    try:
        return item.getVideoInfoTag(offscreen=True)
    except TypeError:
        return item.getVideoInfoTag()


def _try(method, *args):
    try:
        method(*args)
        return True
    except Exception:
        return False


def apply_playback_identity(item, ctx, info=None, force=False,
                            apply_video_tag=True):
    """Annotate the final ListItem and return True when the bridge ran.

    ``force`` exists for deterministic tests.  Production calls always use
    automatic installed/enabled detection.
    """
    if item is None or (not force and not is_available()):
        return False

    identity = playback_identity(ctx, info=info)
    # A TV lookup without both episode numbers cannot match TheIntroDB.  Keep
    # playback untouched; the service may still identify a movie by IDs.
    if identity['is_episode'] and not (
            identity['season'] is not None and identity['episode']):
        return False
    if not (identity['tmdb_id'] or identity['imdb_id']):
        return False

    properties = {
        'dexhub.theintrodb': 'true',
        'mediatype': identity['media_type'],
    }
    if identity['tmdb_id']:
        properties['tmdb_id'] = identity['tmdb_id']
    if identity['imdb_id']:
        properties['imdb_id'] = identity['imdb_id']
        properties['imdbnumber'] = identity['imdb_id']
    if identity['is_episode']:
        properties.update({
            'season': str(identity['season']),
            'episode': str(identity['episode']),
            'tvshowtitle': identity['show_title'],
        })
        if identity['tmdb_id']:
            properties['tmdbshow'] = identity['tmdb_id']
            properties['tmdb_show'] = identity['tmdb_id']
        if identity['imdb_id']:
            properties['imdbshow'] = identity['imdb_id']

    for key, value in properties.items():
        if value not in (None, ''):
            _try(item.setProperty, key, str(value))

    # TheIntroDB reads VideoInfoTag and Player.GetItem.  Keep this tag tiny so
    # Kodi 22's Dex safe handoff does not regain the heavy artwork/cast tag
    # that the minimal-item mode deliberately avoids.
    try:
        tag = _video_tag(item) if apply_video_tag else None
    except Exception:
        tag = None
    if tag is not None:
        _try(tag.setMediaType, identity['media_type'])
        if identity['title']:
            _try(tag.setTitle, identity['title'])
        if identity['imdb_id']:
            _try(tag.setIMDBNumber, identity['imdb_id'])
        if identity['is_episode']:
            _try(tag.setSeason, identity['season'])
            _try(tag.setEpisode, identity['episode'])
            if identity['show_title']:
                _try(tag.setTvShowTitle, identity['show_title'])

        unique_ids = {}
        if identity['imdb_id']:
            unique_ids['imdb'] = identity['imdb_id']
        if identity['tmdb_id']:
            unique_ids['tmdb'] = identity['tmdb_id']
        if identity['tvdb_id']:
            unique_ids['tvdb'] = identity['tvdb_id']
        if identity['is_episode']:
            # These exact aliases are what TheIntroDB checks for streamed
            # episodes before attempting Kodi-library title fallbacks.
            if identity['tmdb_id']:
                unique_ids['tmdbshow'] = identity['tmdb_id']
                unique_ids['tmdb_show'] = identity['tmdb_id']
                unique_ids['tvshow.tmdb'] = identity['tmdb_id']
            if identity['imdb_id']:
                unique_ids['tvshow.imdb'] = identity['imdb_id']
            if identity['tvdb_id']:
                unique_ids['tvshow.tvdb'] = identity['tvdb_id']
        if unique_ids:
            default = ('imdb' if identity['imdb_id'] else
                       ('tmdbshow' if identity['is_episode'] else 'tmdb'))
            _try(tag.setUniqueIDs, unique_ids, default)

    try:
        xbmc.log(
            '[DexHub][TheIntroDB] playback identity ready: %s tmdb=%s imdb=%s S%sE%s' % (
                identity['media_type'], identity['tmdb_id'] or '-',
                identity['imdb_id'] or '-',
                identity['season'] if identity['season'] is not None else '-',
                identity['episode'] or '-'),
            xbmc.LOGINFO)
    except Exception:
        pass
    return True
