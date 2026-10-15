# -*- coding: utf-8 -*-
"""v5.10.302: the Nuvio profile's custom poster pattern, as Nuvio applies it.

Nuvio keeps a poster URL pattern per profile (layout_settings
custom_poster_url_pattern, an RPDB, top-posters or AIOMetadata style link)
and the screens it applies to (custom_poster_enabled_screens: missing means
every screen, "none" none). A card's poster is the pattern filled with the
card's ids; when a required id is missing the add-on's own poster stays.
This is a port of Nuvio TV's CustomPosterUrlResolver and its overlay rules
(core/poster/*.kt): the same placeholders ({id}, {id_type}, {typed_id},
{type}, {shape}, {imdb_id}, {tmdb_id}, {tvdb_id}, {kitsu_id}, {anilist_id},
{mal_id}, {anidb_id}), optional {x?}, alternatives {a|b}, and the RPDB
family's id swap (imdb, tmdb, tvdb).
"""
SCREENS = ('home', 'continue_watching', 'collections', 'library', 'search', 'details',
           'tv_recommendations')
_KEYS = ('id', 'id_type', 'typed_id', 'type', 'shape', 'imdb_id', 'tmdb_id', 'tvdb_id',
         'kitsu_id', 'anilist_id', 'mal_id', 'anidb_id')
_RPDB = ('ratingposterdb.com', 'aioratings.com', 'top-posters.com', 'btttr.cc')
_PREPARED = {}


def settings():
    """(pattern, enabled screens) of the signed-in Nuvio profile."""
    try:
        from . import nuvio_profile_prefs
        layout, synced = nuvio_profile_prefs.layout_settings()
    except Exception:
        return '', frozenset()
    if not synced:
        return '', frozenset()
    pattern = str(layout.get('custom_poster_url_pattern') or '').strip()
    raw = layout.get('custom_poster_enabled_screens')
    if raw is None:
        screens = frozenset(SCREENS)
    else:
        if isinstance(raw, (list, tuple, set)):
            keys = set(str(x).strip() for x in raw if str(x).strip())
        else:
            keys = set(x.strip() for x in str(raw).split(',') if x.strip())
        screens = frozenset() if (not keys or keys == {'none'}) else frozenset(k for k in keys if k in SCREENS)
    return pattern, screens


def pattern_for(screen):
    pattern, screens = settings()
    return pattern if pattern and screen in screens else ''


def _decode(pattern):
    if '%' not in pattern:
        return pattern
    for a, b in (('%7B', '{'), ('%7b', '{'), ('%7D', '}'), ('%7d', '}'), ('%7C', '|'), ('%7c', '|')):
        pattern = pattern.replace(a, b)
    return pattern


def _tokens(decoded):
    out = []
    index = 0
    while True:
        start = decoded.find('{', index)
        if start < 0:
            break
        end = decoded.find('}', start + 1)
        if end < 0:
            break
        body = decoded[start + 1:end]
        optional = body.endswith('?')
        if optional:
            body = body[:-1]
        keys = body.split('|') if body else []
        if keys and all(k and all(c.islower() or c == '_' for c in k) for k in keys):
            out.append((start, end + 1, optional, keys))
            index = end + 1
        else:
            index = start + 1
    return out


def _prepared(pattern):
    hit = _PREPARED.get(pattern)
    if hit is None:
        decoded = _decode(pattern)
        hit = (decoded, _tokens(decoded), any(d in decoded for d in _RPDB))
        if len(_PREPARED) > 32:
            _PREPARED.clear()
        _PREPARED[pattern] = hit
    return hit


def extract_ids(meta_id, imdb_id='', extra=None):
    meta_id = str(meta_id or '').strip()
    ids = {'id': meta_id}
    for prefix, key in (('tmdb:', 'tmdb_id'), ('tvdb:', 'tvdb_id'), ('kitsu:', 'kitsu_id'),
                        ('anilist:', 'anilist_id'), ('mal:', 'mal_id'), ('anidb:', 'anidb_id')):
        if meta_id.startswith(prefix):
            ids[key] = meta_id[len(prefix):]
    if meta_id.startswith('tt'):
        ids['imdb_id'] = meta_id
    if imdb_id:
        ids['imdb_id'] = str(imdb_id).strip()
    for key, value in (extra or {}).items():
        # the ids Dex Hub already knows fill the gaps (Nuvio reads them from
        # the meta id only; a pattern needing one keeps working either way)
        if value and not ids.get(key) and key in _KEYS:
            ids[key] = str(value).strip()
    return ids


def _raw_and_type(ids):
    meta_id = ids.get('id') or ''
    if meta_id.startswith('tt'):
        return meta_id, 'imdb'
    if ':' in meta_id:
        prefix, raw = meta_id.split(':', 1)
        return raw, prefix
    return meta_id, 'unknown'


def _fill(decoded, tokens, ids, kind, shape):
    raw, id_type = _raw_and_type(ids)
    if id_type == 'imdb':
        typed = raw
    elif id_type in ('tmdb', 'tvdb'):
        typed = '%s-%s' % ('movie' if kind == 'movie' else 'series', raw)
    else:
        typed = raw

    def lookup(key):
        if key == 'id':
            return ids.get('id') or ''
        if key == 'id_type':
            return id_type
        if key == 'typed_id':
            return typed
        if key == 'type':
            return kind
        if key == 'shape':
            return shape
        if key in _KEYS:
            return ids.get(key) or ''
        return None

    out = []
    cursor = 0
    for start, end, optional, keys in tokens:
        out.append(decoded[cursor:start])
        if len(keys) > 1:
            value = next((v for v in (lookup(k) for k in keys) if v), None)
            if value is None:
                return None
            out.append(value)
        else:
            value = lookup(keys[0])
            if value is None:
                out.append(decoded[start:end])
            elif not value and not optional:
                return None
            else:
                out.append(value)
        cursor = end
    out.append(decoded[cursor:])
    return ''.join(out)


def resolve(pattern, ids, kind, shape='poster'):
    if not pattern or not pattern.strip():
        return None
    decoded, tokens, rpdb = _prepared(pattern)
    if not tokens:
        return None
    direct = _fill(decoded, tokens, ids, kind, shape)
    if direct is not None or not rpdb:
        return direct
    prefix = 'movie' if kind == 'movie' else 'series'
    tmdb, tvdb, imdb = ids.get('tmdb_id'), ids.get('tvdb_id'), ids.get('imdb_id')
    tries = []
    if '{imdb_id}' in decoded:
        if tmdb:
            tries.append(decoded.replace('/imdb/', '/tmdb/').replace('{imdb_id}', '%s-%s' % (prefix, tmdb)))
        if tvdb:
            tries.append(decoded.replace('/imdb/', '/tvdb/').replace('{imdb_id}', '%s-%s' % (prefix, tvdb)))
    elif '{tmdb_id}' in decoded:
        if imdb:
            tries.append(decoded.replace('/tmdb/', '/imdb/').replace('%s-{tmdb_id}' % prefix, imdb).replace('{tmdb_id}', imdb))
        if tvdb:
            tries.append(decoded.replace('/tmdb/', '/tvdb/').replace('{tmdb_id}', tvdb))
    elif '{tvdb_id}' in decoded:
        if imdb:
            tries.append(decoded.replace('/tvdb/', '/imdb/').replace('%s-{tvdb_id}' % prefix, imdb).replace('{tvdb_id}', imdb))
        if tmdb:
            tries.append(decoded.replace('/tvdb/', '/tmdb/').replace('{tvdb_id}', tmdb))
    for candidate in tries:
        value = _fill(candidate, _tokens(candidate), ids, kind, shape)
        if value is not None:
            return value
    return None


def _kind(media_type):
    value = str(media_type or '').strip().lower()
    return 'movie' if value in ('movie', 'film') else 'series'


def apply(meta, screen, media_type='', extra_ids=None, pattern=None):
    """The card with the profile's poster (a copy; the same card when the
    screen is off, no pattern is set or an id is missing). The add-on's
    poster stays in raw_poster for a fallback."""
    if not isinstance(meta, dict):
        return meta
    if pattern is None:
        pattern = pattern_for(screen)
    if not pattern:
        return meta
    shape_ok = '{shape}' in _decode(pattern)
    shape = str(meta.get('posterShape') or 'poster').strip().lower() or 'poster'
    if shape == 'regular':
        shape = 'poster'
    if not shape_ok and shape != 'poster' and screen != 'details':
        return meta
    ids = extract_ids(meta.get('id') or meta.get('canonical_id') or '',
                      meta.get('imdb_id') or meta.get('imdbId') or '', extra_ids)
    kind = _kind(media_type or meta.get('type') or meta.get('media_type'))
    poster = resolve(pattern, ids, kind, shape if shape_ok and screen != 'details' else 'poster')
    landscape = resolve(pattern, ids, kind, 'landscape') if shape_ok else None
    if poster is None and landscape is None:
        return meta
    out = dict(meta)
    if poster:
        out.setdefault('raw_poster', meta.get('poster') or '')
        out['poster'] = poster
    if landscape:
        out['landscape'] = landscape
        out['landscapePoster'] = landscape
    out['_nuvio_custom_poster'] = '1'
    return out


def apply_all(metas, screen, media_type=''):
    pattern = pattern_for(screen)
    if not pattern:
        return list(metas or [])
    return [apply(m, screen, media_type, pattern=pattern) for m in (metas or [])]
