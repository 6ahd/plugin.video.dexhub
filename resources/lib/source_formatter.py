# -*- coding: utf-8 -*-
"""Small, safe formatter for the source identity line.

The syntax intentionally follows the useful part of the AIOStreams formatter
language.  It is evaluated only while source rows are created; moving focus in
the Kodi window never invokes it again.
"""
from functools import lru_cache
import re


DEFAULT_TEMPLATE = (
    '{stream.type::exists["[{stream.type}] "||""]}'
    '{stream.indexer::exists["{stream.indexer}"||"{addon.name}"]}'
    '{service.shortName::exists[" · {service.shortName}"||""]}'
)


def _clean(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return list(value)
    return str(value).strip()


def _truthy(value):
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() not in ('', '0', 'false', 'no', 'none', 'null')


def _human_size(value):
    text = str(value or '').strip()
    if not text:
        return ''
    # Already human readable: do not accidentally turn "8.4 GB" into bytes.
    if re.search(r'(?i)\b(?:kb|mb|gb|tb|kib|mib|gib|tib)\b', text):
        return text
    try:
        number = float(value)
    except Exception:
        return text
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    unit = 0
    while number >= 1024.0 and unit < len(units) - 1:
        number /= 1024.0
        unit += 1
    return ('%.0f %s' if unit == 0 else '%.2f %s') % (number, units[unit])


def build_context(row, facts):
    row = row or {}
    facts = facts or {}
    hints = row.get('behaviorHints') or {}
    if not isinstance(hints, dict):
        hints = {}
    source_type = (facts.get('source_type') or ('', '', ''))[0]
    # AIO calls raw torrents p2p. Keep that alias for existing presets while
    # exposing USENET/DEBRID/native servers as their real distinct type.
    formatter_type = ('P2P' if source_type == 'torrent' else
                      str(source_type or '').upper())
    video = [str(x or '') for x in (facts.get('video_bits') or [])]
    resolution = next((x for x in video if re.match(r'(?i)^(?:2160|1080|720|576|480)p$', x)), '')
    if resolution:
        resolution = resolution[:-1] + 'p'
    visual = list((facts.get('tags') or {}).get('visual') or [])
    size_value = (row.get('sizebytes') or row.get('sizeBytes') or
                  hints.get('videoSize') or row.get('size') or facts.get('size') or '')
    service = facts.get('service') or ''
    short_service = {
        'REAL-DEBRID': 'RD+', 'REALDEBRID': 'RD+',
        'ALLDEBRID': 'AD+', 'ALL-DEBRID': 'AD+',
        'PREMIUMIZE': 'PM', 'TORBOX': 'TB',
        'EASYDEBRID': 'ED+', 'EASY-DEBRID': 'ED+',
    }.get(str(service).upper(), service)
    release_group = (row.get('releaseGroup') or row.get('release_group') or
                     hints.get('releaseGroup') or '')
    cached = row.get('cached')
    if cached is None:
        cached = hints.get('cached')
    return {
        'stream.indexer': facts.get('indexer') or '',
        'stream.resolution': resolution,
        'stream.visualTags': visual,
        'stream.size': size_value,
        'stream.releaseGroup': release_group,
        'stream.type': formatter_type,
        'stream.proxied': row.get('proxied', hints.get('proxied', False)),
        'stream.private': row.get('private', hints.get('private', False)),
        'stream.library': row.get('library', hints.get('library', False)),
        'stream.seadex': row.get('seadex', False),
        'stream.seadexBest': row.get('seadexBest', False),
        'stream.rseMatched': row.get('rseMatched') or '',
        'stream.regexMatched': row.get('regexMatched') or '',
        'addon.name': facts.get('addon') or '',
        'service.shortName': short_service,
        'service.cached': cached if cached is not None else False,
    }


def _find_close(text, start):
    depth = 0
    quote = ''
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in ('"', "'"):
            quote = char
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
    return -1


@lru_cache(maxsize=24)
def _compile(template):
    nodes = []
    cursor = 0
    literal = []
    while cursor < len(template):
        if template.startswith('{?', cursor):
            close = template.find('?}', cursor + 2)
            if close >= 0:
                if literal:
                    nodes.append(('text', ''.join(literal)))
                    literal = []
                nodes.append(('optional', template[cursor + 2:close]))
                cursor = close + 2
                continue
        if template[cursor] == '{':
            close = _find_close(template, cursor)
            if close >= 0:
                if literal:
                    nodes.append(('text', ''.join(literal)))
                    literal = []
                nodes.append(('expr', template[cursor + 1:close]))
                cursor = close + 1
                continue
        literal.append(template[cursor])
        cursor += 1
    if literal:
        nodes.append(('text', ''.join(literal)))
    return tuple(nodes)


def _branch(expr):
    marker = expr.find('["')
    if marker < 0 or not expr.endswith('"]'):
        return expr, None
    body = expr[marker + 2:-2]
    split = body.find('"||"')
    if split < 0:
        return expr, None
    return expr[:marker], (body[:split], body[split + 4:])


def _condition_term(term, context):
    bits = term.split('::')
    value = context.get(bits[0].strip(), '')
    result = _truthy(value)
    for operation in bits[1:]:
        op = operation.strip().replace('\\~', '~')
        low = op.lower()
        if low == 'exists':
            result = value not in (None, '', [], ())
        elif low == 'isfalse':
            result = not _truthy(value)
        elif low == 'string':
            value = str(value or '')
            result = bool(value)
        elif op.startswith('~'):
            result = op[1:].casefold() in str(value or '').casefold()
        elif op.startswith('='):
            result = str(value or '').casefold() == op[1:].casefold()
        elif op.startswith('>'):
            try:
                result = float(value or 0) > float(op[1:] or 0)
            except Exception:
                result = False
    return result


def _condition(expression, context):
    # AND binds more tightly than OR, matching the common formatter presets.
    groups = expression.split('::or::')
    return any(all(_condition_term(term, context)
                   for term in group.split('::and::')) for group in groups)


def _transform(expression, context):
    bits = expression.split('::')
    value = context.get(bits[0].strip(), '')
    for operation in bits[1:]:
        op = operation.strip()
        match = re.match(r"replace\(\s*(['\"])(.*?)\1\s*,\s*(['\"])(.*?)\3\s*\)$", op)
        if match:
            value = str(value or '').replace(match.group(2), match.group(4))
            continue
        match = re.match(r"join\(\s*(['\"])(.*?)\1\s*\)$", op)
        if match:
            value = match.group(2).join(str(x) for x in (value if isinstance(value, (list, tuple)) else [value]) if x)
            continue
        match = re.match(r'truncate\(\s*(\d+)\s*\)$', op)
        if match:
            value = str(value or '')[:int(match.group(1))]
            continue
        if op.lower() == 'first':
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ''
            else:
                value = str(value or '').split(',')[0].strip()
            continue
        if op.lower() == 'sbytes':
            value = _human_size(value)
    if isinstance(value, (list, tuple)):
        return '/'.join(str(x) for x in value if x)
    if isinstance(value, bool):
        return 'true' if value else ''
    return str(value or '')


def _render(template, context):
    output = []
    for kind, value in _compile(str(template or '')):
        if kind == 'text':
            output.append(value)
        elif kind == 'optional':
            rendered = _render(value, context)
            if rendered.strip(' ·•|-/'):
                output.append(rendered)
        else:
            expression, branches = _branch(value)
            if branches is not None:
                output.append(_render(branches[0] if _condition(expression, context)
                                      else branches[1], context))
            else:
                output.append(_transform(expression, context))
    return ''.join(output)


def render(template, row, facts):
    try:
        return re.sub(r'\s+', ' ', _render(template, build_context(row, facts))).strip(' ·•|')
    except Exception:
        return ''
