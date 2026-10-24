"""Compact Home facts; full metadata stays available on the title page."""
import re


def cached_meta(item):
    """Upgrade cached display facts at render time, without refetching rows."""
    props, info = item.get('props') or {}, item.get('info') or {}
    if 'dhs.hero_meta' in props:
        return str(props.get('dhs.hero_meta') or '')
    if props.get('kind') != 'work':
        return str(props.get('dhs.meta') or '')
    parts = []
    def number(key):
        try:
            return max(0, int(float(info.get(key) or 0)))
        except (TypeError, ValueError, OverflowError):
            return 0
    year, duration = number('year'), number('duration')
    if 1800 <= year <= 2200:
        parts.append(str(year))
    if duration:
        minutes = duration // 60
        arabic = bool(re.search(r'\d\s*[سد]', str(props.get('dhs.meta') or '')))
        h, m = ('س', 'د') if arabic else ('h', 'm')
        parts.append(('%d%s %d%s' % (minutes // 60, h, minutes % 60, m))
                     if minutes >= 60 else '%d%s' % (minutes, m))
    genres = info.get('genres') or info.get('genre') or []
    if isinstance(genres, str):
        genres = [s.strip() for s in re.split(r'\s*[/,|]\s*', genres) if s.strip()]
    if genres:
        parts.append(' / '.join(str(s) for s in genres[:2]))
    if info.get('mediatype') == 'episode' and number('episode'):
        parts.append('S%d:E%d' % (number('season'), number('episode')))
    return '   •   '.join(parts)

def compact_meta(text, certificate='', episode=''):
    parts = []
    for part in str(text or '').split('   •   '):
        part = part.strip()
        if not part or (certificate and part == str(certificate).strip()):
            continue
        if episode and (part == episode or part.startswith(episode + '  ·  ')):
            part = episode
        parts.append(part)
    return '   •   '.join(parts)
