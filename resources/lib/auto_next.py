# -*- coding: utf-8 -*-
"""v5.10.448: a dead link gives way to the next source of the same list.

A source that never played (Kodi's error before any progress, or no link at
all from its resolver) is followed by the next row of the list the title's
sources were shown in (dexhub.last_streams, the player's Sources panel's
list), from the same resume point, up to TRIES times a title. A stop by the
user, or a source that played and ended, never does this (companion.py
decides). Setting: auto_next_source (on by default).
"""
import json
import time

import xbmc
import xbmcgui

TRIES = 3
_STATE = 'dexhub.autonext'
_FRESH = 600            # a title's tries count for ten minutes
_TWICE = 20             # the same failure reported twice (error, then stop)


def enabled():
    try:
        from .settings_cache import cached_addon
        return (cached_addon().getSetting('auto_next_source') or 'true').lower() != 'false'
    except Exception:
        return True


def _item_key(ctx):
    from .skinui.osd_sources import _item_key as key
    return key(ctx.get('canonical_id'), ctx.get('season'), ctx.get('episode'))


def _state(home, item):
    try:
        state = json.loads(home.getProperty(_STATE) or '{}')
    except ValueError:
        state = {}
    if state.get('item') != item or time.time() - float(state.get('t') or 0) > _FRESH:
        state = {'item': item, 'tried': [], 't': time.time(), 'last': '', 'last_t': 0}
    return state


def _rows(home, item):
    try:
        data = json.loads(home.getProperty('dexhub.last_streams') or '{}')
    except ValueError:
        return []
    if data.get('item') != item:
        return []
    return [row for row in data.get('rows') or [] if row.get('k')]


def try_next(ctx, reason=''):
    """Play the next untried source of ctx's title; False when there is none
    (or this failure was handled a moment ago)."""
    if not enabled() or not isinstance(ctx, dict):
        return False
    current = str(ctx.get('stream_key') or '')
    if not current or not (ctx.get('canonical_id') or ctx.get('video_id')):
        return False
    home = xbmcgui.Window(10000)
    item = _item_key(ctx)
    state = _state(home, item)
    if state.get('last') == current and time.time() - float(state.get('last_t') or 0) < _TWICE:
        return False
    tried = list(state.get('tried') or [])
    if current not in tried:
        tried.append(current)
    state.update(tried=tried, last=current, last_t=time.time())
    if len(tried) > TRIES:
        home.setProperty(_STATE, json.dumps(state))
        _notify('ما اشتغل ولا مصدر من المصادر اللي جربناها')
        return False
    from . import cache_store
    following = next((row for row in _rows(home, item)
                      if str(row['k']) not in tried and cache_store.get('stream', str(row['k']))), None)
    if following is None:
        home.setProperty(_STATE, json.dumps(state))
        _notify('الرابط ما اشتغل، وما فيه مصدر ثاني بالقائمة')
        return False
    key = str(following['k'])
    home.setProperty(_STATE, json.dumps(state))
    xbmc.log('[DexHub] auto next source (%s): %s -> %s, try %d of %d' % (reason, current, key, len(tried), TRIES),
             xbmc.LOGINFO)
    _notify('الرابط ما اشتغل، نجرب المصدر اللي بعده (%d/%d)', (len(tried), TRIES))
    from urllib.parse import urlencode
    query = {'action': 'play', 'stream_key': key}
    try:
        if float(ctx.get('resume_seconds') or 0) > 1.0:
            query['resume_seconds'] = '%.3f' % float(ctx['resume_seconds'])
        if 1.0 < float(ctx.get('resume_percent') or 0) < 95.0:
            query['resume_percent'] = '%.2f' % float(ctx['resume_percent'])
    except (TypeError, ValueError):
        pass
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?%s)' % urlencode(query))
    return True


def _notify(text, args=None):
    try:
        from .i18n import tr
        text = tr(text)
    except Exception:
        pass
    if args:
        text = text % args
    try:
        xbmcgui.Dialog().notification('Dex Hub', text, xbmcgui.NOTIFICATION_INFO, 4000)
    except Exception:
        pass
