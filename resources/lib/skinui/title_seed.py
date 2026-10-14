"""Exact-identity metadata handoff from a Home card to its details dialog."""
from copy import deepcopy
import json
from urllib.parse import parse_qsl
from . import common as C
from .. import profile_scope


def cached_item(query):
    if not profile_scope.is_current():
        return None
    wanted = str(query.get('i') or '')
    if not wanted:
        return None
    tab = C.prop('dhs.tab') or 'all'
    if tab not in C.TABS:
        return None
    from . import board
    row_ids = [board.SPOT] + [spec['id'] for _, spec in board.published(tab)]
    candidates = []
    for row_id in row_ids:
        data = C.read_row(C.row_file(tab, row_id)) or {}
        for item in data.get('items') or []:
            props = item.get('props') or {}
            if props.get('kind') == 'work' and props.get('dhs.id') == wanted:
                candidate_q = dict(parse_qsl(props.get('dhs.q') or ''))
                if any(query.get(k) and candidate_q.get(k) and query[k] != candidate_q[k]
                       for k in ('srv', 'src', 'm')):
                    continue
                info = item.get('info') or {}
                score = sum(bool(info.get(k)) for k in ('plot', 'genres', 'rating', 'year', 'duration'))
                if info.get('mediatype') == 'episode':
                    score -= 10
                candidates.append((score, item))
    if candidates and profile_scope.is_current():
        # A Spotlight card can have the logo while another row has the plot
        # and IDs. Keep the best card's route and fill its missing facts.
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        result = deepcopy(candidates[0][1])
        for _score, candidate in candidates[1:]:
            episode = (candidate.get('info') or {}).get('mediatype') == 'episode'
            for section in ('info', 'art', 'ids', 'props'):
                dest = result.setdefault(section, {})
                for key, value in (candidate.get(section) or {}).items():
                    if episode and section == 'info':
                        continue
                    if episode and section == 'art' and key not in ('fanart', 'clearlogo'):
                        continue
                    if value and not dest.get(key):
                        dest[key] = deepcopy(value)
        return result
    return None


def seed_hero(hero, query):
    """Fill absent title facts from its exact cached card, in every metadata mode."""
    item = cached_item(query)
    if not item:
        return
    info, art = item.get('info') or {}, item.get('art') or {}
    episode = info.get('mediatype') == 'episode'
    values = {k: info.get(k) for k in ('title', 'plot', 'year', 'genres', 'rating', 'duration', 'tagline')}
    values.update({k: art.get(k) for k in ('poster', 'fanart', 'landscape', 'clearlogo')})
    values.update({k + '_id': v for k, v in (item.get('ids') or {}).items() if k in ('imdb', 'tmdb', 'tvdb')})
    try:
        ratings = json.loads((item.get('props') or {}).get('dhs.ratings') or '{}')
        if isinstance(ratings, dict):
            values['ratings'] = ratings
    except (TypeError, ValueError):
        pass
    if episode:
        values = {'title': info.get('tvshowtitle'), 'fanart': art.get('fanart'), 'clearlogo': art.get('clearlogo')}
    for key, value in values.items():
        if value and not hero.get(key):
            hero[key] = value
