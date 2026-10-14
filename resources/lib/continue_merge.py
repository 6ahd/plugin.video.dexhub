"""Merge display cards locally; never rewrite progress or playback routes."""


def _stamp(row):
    try:
        from .native_continue import _epoch
        value = _epoch(row.get('updated_at'))
        return value / 1000.0 if value > 1e12 else value
    except Exception:
        return 0.0


def _episode(row):
    return str(row.get('media_type') or '').lower() in ('series', 'episode', 'anime', 'tv') \
        or bool(row.get('season') or row.get('episode'))


def _number(value):
    text = str(value if value is not None else '').strip()
    return str(int(text)) if text.isdigit() else text


def _same_episode(a, b):
    return bool(_number(a.get('episode'))) and all(
        _number(a.get(k)) == _number(b.get(k)) for k in ('season', 'episode'))


def _good_name(value):
    import re
    text = str(value or '').strip()
    return bool(text) and not re.match(r'^(?:tt\d+|\d+|episode\s*\d+|s\d+\s*e\d+|الحلقة\s*\d+)$', text, re.I)


def merge(rows, keys, placeholder=lambda value: False):
    rows = [dict(r) for r in rows or []]
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owners = {}
    aliases = [set(keys(row)) for row in rows]
    for i, row in enumerate(rows):
        for key in aliases[i]:
            if key in owners:
                a, b = find(i), find(owners[key])
                if a != b:
                    parent[a] = b
            else:
                owners[key] = i
    # A server can expose only its episode ids, while the local account owns
    # the show's ids. Bridge its show name only when that name resolves to
    # exactly one identified show in this shelf; ambiguous names stay apart.
    import re
    named = {}
    for i, row in enumerate(rows):
        name = row.get('show_title')
        if not _episode(row) or not _good_name(name):
            continue
        name = re.sub(r'[^\w]+', ' ', str(name).lower()).strip()
        strong = any(k.startswith(('s:tt', 's:tmdb')) for k in aliases[i])
        named.setdefault(name, []).append((i, strong))
    for entries in named.values():
        identified = {find(i) for i, strong in entries if strong}
        if len(identified) == 1:
            target = next(iter(identified))
            for i, strong in entries:
                if not strong:
                    parent[find(i)] = find(target)
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)
    out = []
    for group in groups.values():
        group.sort(key=_stamp, reverse=True)
        newest = dict(group[0])
        for donor in group[1:]:
            # Artwork and series names belong to the work. Episode plots and
            # titles are copied only for the elected episode, never its sibling.
            for field in ('poster', 'background', 'clearlogo', 'show_title'):
                have, value = newest.get(field), donor.get(field)
                if field == 'show_title':
                    if not _good_name(have) and _good_name(value):
                        newest[field] = value
                elif (not have or placeholder(have)) and value and not placeholder(value):
                    newest[field] = value
            if not _episode(newest) or _same_episode(newest, donor):
                for field in ('title', 'plot', 'year', 'rating', 'genre', 'studio'):
                    missing = not newest.get(field)
                    if field == 'title':
                        missing = not _good_name(newest.get(field))
                    if missing and donor.get(field) and (field != 'title' or _good_name(donor[field])):
                        newest[field] = donor[field]
        out.append(newest)
    out.sort(key=_stamp, reverse=True)
    return out
