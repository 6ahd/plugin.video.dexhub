# -*- coding: utf-8 -*-
"""Profile-local per-row focus behavior; collections on, title recommendations off."""
import json
import os
import threading
from urllib.parse import parse_qsl, urlsplit
from . import profile_scope

_LOCK = threading.RLock()
_CACHE = {}


def _path():
    return os.path.join(profile_scope.path('homeui'), 'follow_rows.json')


def _read():
    path = _path()
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    hit = _CACHE.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    _CACHE[path] = (stamp, data)
    return data


def values(page, key):
    from .ui_preferences import enabled
    with _LOCK:
        section = _read().get(str(page))
        raw = section.get(str(key)) if isinstance(section, dict) else {}
        raw = raw if isinstance(raw, dict) else {}
        return {name: raw[name] if type(raw.get(name)) is bool else default
                for name, default in [('collections', True), ('works', enabled('homeui_follow_works'))]}


def set_value(page, key, name, value):
    if name not in ('collections', 'works') or not key:
        return False
    with _LOCK:
        profile_scope.require_current()
        data = _read()
        row = data.setdefault(str(page), {}).setdefault(str(key), {})
        row[name] = bool(value)
        profile_scope._write(_path(), data)
        _CACHE.pop(_path(), None)
    return True


def reset(page, key):
    with _LOCK:
        profile_scope.require_current()
        data = _read()
        data.get(str(page), {}).pop(str(key), None)
        profile_scope._write(_path(), data)
        _CACHE.pop(_path(), None)


def filter_target(page, key, target):
    from .ui_preferences import enabled
    if not target or not enabled('homeui_follow_rows'):
        return ''
    query = dict(parse_qsl(urlsplit(target).query))
    related = query.get('action') == 'skin_follow_related'
    return target if values(page, key)['works' if related else 'collections'] else ''


def options(page, key, tr):
    state = values(page, key)
    return [(name, tr(label)) for name, label in [
        ('collections', 'الكوليكشنات تحت هذا الصف: مفعّل' if state['collections'] else 'الكوليكشنات تحت هذا الصف: معطّل'),
        ('works', 'توصيات الأعمال تحت هذا الصف: مفعّل' if state['works'] else 'توصيات الأعمال تحت هذا الصف: معطّل'),
        ('reset', 'استعادة إعداد الصف التابع الافتراضي')]]


def edit(page, key, title, tr):
    from .homeui.options import choose
    choices = options(page, key, tr)
    picked = choose(tr('خيارات الصف…'), [label for _, label in choices], subtitle=title)
    if not 0 <= picked < len(choices):
        return False
    name = choices[picked][0]
    if name == 'reset':
        reset(page, key)
    else:
        set_value(page, key, name, not values(page, key)[name])
    from .skinui.common import ask_republish
    ask_republish('dependent row preference')
    return True
