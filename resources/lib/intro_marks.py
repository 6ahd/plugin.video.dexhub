# -*- coding: utf-8 -*-
"""Mark intro (v5.10.566): the intro marked by hand, where TheIntroDB has none.

The player bar's "Mark intro" button (osd_buttons key 'intro', action
intro_mark): pressed where the intro starts, then again where it ends. The
span is kept for the series' season (and for the series, as a fallback for
its other seasons), or for the film, and the skip button offers it on every
episode of that season that TheIntroDB knows no intro for (native_playback).
Pressed on a title that has a mark, it offers to mark it again or to forget it.

Kept in the add-on's profile as intro_marks.json: {key: [start, end]}, keys
tv:<id>:s<season>, tv:<id>, movie:<id> (<id> a TMDb, IMDb or TVDB id).
"""
import json
import os
import time

import xbmc
import xbmcgui

PENDING = 'dexhub.intro_mark.pending'
MIN_SECONDS, MAX_SECONDS = 5, 15 * 60


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return en


def _path():
    import xbmcaddon
    import xbmcvfs
    folder = xbmcvfs.translatePath(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('profile'))
    return os.path.join(folder, 'intro_marks.json')


def _load():
    try:
        with open(_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + '.tmp'
    with open(temp, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    os.replace(temp, path)


def keys(playing):
    """Where a mark is kept for the playing title, the most precise first."""
    q = (playing or {}).get('q') or {}
    ident = ''
    for name in ('tmdb', 'imdb', 'tvdb'):
        if q.get(name):
            ident = '%s%s' % ('' if name == 'tmdb' else name + ':', q[name])
            break
    if not ident:
        return []
    if playing.get('series'):
        season = playing.get('season')
        return (['tv:%s:s%s' % (ident, season)] if season not in (None, '') else []) + ['tv:%s' % ident]
    return ['movie:%s' % ident]


def lookup(playing):
    data = _load()
    for key in keys(playing):
        span = data.get(key)
        if isinstance(span, list) and len(span) == 2:
            return float(span[0]), float(span[1])
    return None


def merge(data, playing, duration):
    """TheIntroDB's segments with the hand-marked intro added when it has none."""
    data = dict(data or {})
    if data.get('intro'):
        return data
    span = lookup(playing)
    if span and 0 <= span[0] < span[1] < duration - 1:
        data['intro'] = [span]
    return data


def _clock(seconds):
    seconds = int(seconds)
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60) if seconds >= 3600 else \
        '%d:%02d' % (seconds // 60, seconds % 60)


def _notify(title, text, ms=4500):
    xbmcgui.Dialog().notification(title, text, xbmcgui.NOTIFICATION_INFO, ms, False)


def mark():
    """The player bar's Mark intro button."""
    player = xbmc.Player()
    if not player.isPlayingVideo():
        return
    title = _t('تحديد المقدمة', 'Mark intro')
    try:
        from .native_playback import context, playing_identity
        active = context(player, fresh=True)          # (playback token, its context) or None
        found = keys(playing_identity(active[1])) if active else []
        position = float(player.getTime())
        import hashlib
        # a fingerprint of the file, not its address (which can carry a server's key)
        playing_file = hashlib.sha1(player.getPlayingFile().encode('utf-8', 'replace')).hexdigest()[:16]
    except Exception as exc:
        xbmc.log('[DexHub] intro mark: %s' % exc, xbmc.LOGWARNING)
        found = []
    if not found:
        _notify(title, _t('متاح للأعمال اللي يشغّلها Dex Hub', 'Works for titles Dex Hub plays'))
        return
    home = xbmcgui.Window(10000)
    try:
        pending = json.loads(home.getProperty(PENDING) or '{}')
    except ValueError:
        pending = {}
    same = pending.get('file') == playing_file and pending.get('key') == found[0] \
        and time.time() - float(pending.get('at') or 0) < 30 * 60
    if same:
        start = float(pending.get('start') or 0)
        home.clearProperty(PENDING)
        if not MIN_SECONDS <= position - start <= MAX_SECONDS:
            _notify(title, _t('النهاية لازم تكون بعد البداية بـ5 ثواني إلى 15 دقيقة، ابدأ من جديد',
                              'The end must come 5 seconds to 15 minutes after the start; start again'))
            return
        data = _load()
        data[found[0]] = [round(start, 1), round(position, 1)]
        if len(found) > 1:
            data.setdefault(found[1], [round(start, 1), round(position, 1)])   # the series' other seasons
        _save(data)
        _notify(title, _t('المقدمة %s – %s · تتخطاها بقية حلقات الموسم', 'Intro %s – %s · the season\'s other episodes skip it')
                % (_clock(start), _clock(position)), 6000)
        return
    data = _load()
    if found[0] in data and not pending:
        choice = xbmcgui.Dialog().select(title, [_t('تحديد المقدمة من جديد', 'Mark the intro again'),
                                                 _t('نسيان المقدمة المحددة', 'Forget the marked intro')])
        if choice < 0:
            return
        if choice == 1:
            for key in found:
                data.pop(key, None)
            _save(data)
            _notify(title, _t('نُسيت المقدمة المحددة', 'The marked intro is forgotten'))
            return
    home.setProperty(PENDING, json.dumps({'file': playing_file, 'key': found[0], 'start': position, 'at': time.time()}))
    _notify(title, _t('بداية المقدمة %s · اضغط الزر مرة ثانية عند نهايتها', 'Intro starts at %s · press again where it ends')
            % _clock(position), 6000)
