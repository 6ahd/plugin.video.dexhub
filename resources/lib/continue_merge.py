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
    from decimal import Decimal, InvalidOperation
    if value in (None, '') or isinstance(value, bool):
        return ''
    try:
        n = Decimal(str(value).strip())
        return str(int(n)) if n.is_finite() and n >= 0 and n == int(n) else ''
    except (InvalidOperation, ValueError, OverflowError):
        return ''


def _same_episode(a, b):
    return all(_number(r.get(k)) != '' for r in (a, b) for k in ('season', 'episode')) and all(
        _number(a.get(k)) == _number(b.get(k)) for k in ('season', 'episode'))


def _good_name(value):
    import re
    text = str(value or '').strip()
    if text.casefold() in ('series', 'tv show', 'unknown', 'untitled', 'بدون عنوان', 'مسلسل', 'حلقة'):
        return False
    return bool(text) and not re.match(r'^(?:tt\d+|\d{5,}|episode\s*\d+|s\d+\s*e\d+|الحلقة\s*\d+)$', text, re.I)


def merge(rows, keys, placeholder=lambda value: False, show_name=None, exact_only=False):
    rows = [dict(r) for r in rows or []]
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owners = {}
    aliases = [{key for key in keys(row) if not key.startswith('s:n:') or _good_name(row.get('show_title'))}
               for row in rows]
    if exact_only:
        aliases = [{key + '|ep:' + _number(row.get('season')) + ':' + _number(row.get('episode'))
                    for key in entries} if _episode(row) and _number(row.get('episode')) != '' and _number(row.get('season')) != '' else
                   {'separate:%d' % i}
                   for i, (row, entries) in enumerate(zip(rows, aliases))]
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
    def normalized(value):
        import unicodedata
        return re.sub(r'[^\w]+', ' ', unicodedata.normalize('NFKC', str(value or '')).casefold()).strip()

    def richness(row):
        return sum(bool(row.get(f)) for f in ('poster', 'background', 'plot', 'clearlogo', 'show_title'))

    def compatible(i, j):
        # Conflicting IDs in the SAME namespace or known remake years are not aliases.
        for namespace in ('s:tt', 's:tmdb'):
            a = {k.split('|ep:')[0] for k in aliases[i] if k.startswith(namespace)}
            b = {k.split('|ep:')[0] for k in aliases[j] if k.startswith(namespace)}
            if a and b and not a.intersection(b):
                return False
        a, b = str(rows[i].get('year') or '')[:4], str(rows[j].get('year') or '')[:4]
        return not (a.isdigit() and b.isdigit() and a != b)

    named = {}
    for i, row in enumerate(rows):
        if not _episode(row):
            continue
        name = row.get('show_title')
        if not _good_name(name) and show_name:
            name = show_name(row)  # local cache only, supplied by the listing
        if not _good_name(name):
            continue
        name = normalized(name)
        strong = any(k.startswith(('s:tt', 's:tmdb')) for k in aliases[i])
        named.setdefault(name, []).append((i, strong))
    for entries in named.values():
        identified = {find(i) for i, strong in entries if strong}
        if len(identified) == 1:
            target = next(iter(identified))
            for i, strong in entries:
                if not strong and compatible(i, target) and (not exact_only or _same_episode(rows[i], rows[target])):
                    parent[find(i)] = find(target)
        # An incomplete cached card may carry a different id namespace than
        # its fully populated copy. Match the exact episode to ONE rich group,
        # without trusting an episode's generic ID as the show's ID.
        for i, _strong in entries:
            if richness(rows[i]) >= 3:
                continue
            candidates = {find(j) for j, strong in entries
                          if j != i and strong and richness(rows[j]) >= 3
                          and _same_episode(rows[i], rows[j]) and compatible(i, j)}
            if len(candidates) == 1:
                target = next(iter(candidates))
                if find(i) != find(target):
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
