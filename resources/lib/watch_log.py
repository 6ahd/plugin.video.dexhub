# -*- coding: utf-8 -*-
"""How long you watched, per day (v5.10.334): the island's week.

The playback store keeps one row per title with its last position, not the
time spent. Each local save (the player's progress, a stop) adds what the
position moved since the row's last save to today's total, when that is
between a second and fifteen minutes (a jump or a resume from another
device is not watching time). A title that reaches 90 % counts as finished
that day (an episode or a film). Fifteen days are kept, in the profile's
homeui/watch_week.json.
"""
import datetime
import json
import os
import threading
import uuid

_LOCK = threading.Lock()
KEEP_DAYS = 15
MAX_STEP = 900.0


def _path():
    from . import profile_scope
    return profile_scope.path('homeui/watch_week.json')


def _read():
    try:
        with open(_path(), encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = '%s.%s.tmp' % (path, uuid.uuid4().hex)
    try:
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def record(rows):
    """rows: the playback rows just saved, each with '_old_position' (None
    for a row that was not there)."""
    today = datetime.date.today()
    with _LOCK:
        data = _read()
        day = data.setdefault(today.isoformat(), {'sec': 0, 'titles': [], 'done': []})
        changed = False
        for row in rows or []:
            position = _num(row.get('position'))
            old = row.get('_old_position')
            step = position - _num(old) if old is not None else position
            media = str(row.get('media_type') or '').lower()
            title_key = '%s|%s' % (media, row.get('canonical_id') or row.get('video_id') or '')
            if 1.0 <= step <= MAX_STEP:
                day['sec'] = int(day.get('sec') or 0) + int(step)
                if title_key not in day['titles']:
                    day['titles'].append(title_key)
                changed = True
            duration = _num(row.get('duration'))
            percent = _num(row.get('percent')) or (100.0 * position / duration if duration else 0.0)
            if percent >= 90.0 and step > 0:
                kind = 'ep' if media in ('series', 'episode', 'tv', 'show') or row.get('episode') else 'film'
                done_key = '%s|%s' % (kind, row.get('video_id') or row.get('canonical_id') or '')
                if done_key not in day['done']:
                    day['done'].append(done_key)
                    changed = True
        if not changed:
            return
        oldest = (today - datetime.timedelta(days=KEEP_DAYS)).isoformat()
        for key in [k for k in data if k < oldest]:
            data.pop(key, None)
        _write(data)


def _english():
    try:
        from .i18n import is_english
        return bool(is_english())
    except Exception:
        return False


def duration_text(seconds):
    seconds = int(seconds or 0)
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    if _english():
        return ('%d h %d min' % (hours, minutes)) if hours else '%d min' % minutes
    return ('%d س %d د' % (hours, minutes)) if hours else '%d د' % minutes


_LETTERS_EN = ('M', 'T', 'W', 'T', 'F', 'S', 'S')
_LETTERS_AR = ('ن', 'ث', 'ر', 'خ', 'ج', 'س', 'ح')


def week():
    """The last seven days, today last: total seconds, each day's share of
    the busiest (0 to 100), its letter, and the counts."""
    today = datetime.date.today()
    data = _read()
    days, titles, done = [], set(), set()
    for back in range(6, -1, -1):
        day = today - datetime.timedelta(days=back)
        entry = data.get(day.isoformat()) or {}
        days.append((day, int(entry.get('sec') or 0)))
        titles.update(entry.get('titles') or [])
        done.update(entry.get('done') or [])
    busiest = max([sec for _d, sec in days] + [1])
    letters = _LETTERS_EN if _english() else _LETTERS_AR
    return {'total': sum(sec for _d, sec in days),
            'bars': [(letters[d.weekday()], int(round(100.0 * sec / busiest)) if sec else 0) for d, sec in days],
            'titles': len(titles), 'eps': len([k for k in done if k.startswith('ep|')]),
            'films': len([k for k in done if k.startswith('film|')])}


def publish(home=None):
    """The island's week as Home properties (dhs.panel.week.*)."""
    try:
        if home is None:
            import xbmcgui
            home = xbmcgui.Window(10000)
        got = week()
        english = _english()
        home.setProperty('dhs.panel.week.label', 'WATCHED THIS WEEK' if english else 'شاهدت هذا الأسبوع')
        home.setProperty('dhs.panel.week.total', duration_text(got['total']))
        for index, (letter, share) in enumerate(got['bars']):
            home.setProperty('dhs.panel.week.l%d' % (index + 1), letter)
            # 0..10 steps of the bar (the skin draws ten heights)
            home.setProperty('dhs.panel.week.h%d' % (index + 1), str(int(round(share / 10.0))) if share else '0')
        chips = [('%d titles' if english else '%d عمل') % got['titles'],
                 ('%d episodes' if english else '%d حلقة') % got['eps'],
                 ('%d films' if english else '%d فيلم') % got['films']]
        for index, chip in enumerate(chips):
            home.setProperty('dhs.panel.week.c%d' % (index + 1), chip)
    except Exception:
        pass
