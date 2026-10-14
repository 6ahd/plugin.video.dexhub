# -*- coding: utf-8 -*-
"""Episode calendar using Trakt /calendars endpoints.

For each show on the user's watchlist OR collection, returns the upcoming
(or recently aired) episodes grouped by air date. Lets the user know
when their next favorite episode lands without needing the full Trakt
website experience.

Two modes:
  * 'my'   → only shows the user is watching (Trakt watchlist + collection)
  * 'all'  → all popular new episodes (the public calendar)
"""
from datetime import datetime, timedelta, timezone

from . import trakt
from .log import log


def _today():
    return datetime.now(timezone.utc).date()     # utcnow() is deprecated (Python 3.12+)


def _date_only(s):
    """Return YYYY-MM-DD prefix of ISO timestamp, or empty."""
    if not s:
        return ''
    return str(s)[:10]


def my_shows_calendar(start_date=None, days=14):
    """Return list of upcoming episodes from the user's My Shows.

    Each item: {
        canonical_id, title, season, episode, episode_title,
        first_aired, days_until, poster, fanart, clearlogo,
    }
    """
    if not (trakt.authorized() and trakt.sync_enabled()):
        return []
    if start_date is None:
        start_date = _today()
    days = max(1, min(int(days or 14), 60))
    path = '/calendars/my/shows/%s/%d' % (start_date.strftime('%Y-%m-%d'), days)
    try:
        data = trakt._request(path, method='GET', auth=True, timeout=20)
    except Exception as exc:
        log.warn('CALENDAR', 'my calendar fetch failed: %s', exc)
        return []
    return _normalize_episodes(data)


def public_shows_calendar(start_date=None, days=14):
    """Public calendar (all popular shows). Doesn't require Trakt auth
    but does require Trakt enabled + client_id."""
    if not (trakt.enabled() and trakt.client_id()):
        return []
    if start_date is None:
        start_date = _today()
    days = max(1, min(int(days or 14), 30))
    path = '/calendars/all/shows/%s/%d' % (start_date.strftime('%Y-%m-%d'), days)
    try:
        data = trakt._cached_public_request(path, timeout=20)
    except Exception as exc:
        log.warn('CALENDAR', 'public calendar fetch failed: %s', exc)
        return []
    return _normalize_episodes(data)


def _normalize_episodes(rows):
    if not isinstance(rows, list):
        return []
    out = []
    today = _today()
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            show = r.get('show') or {}
            ep = r.get('episode') or {}
            ids_show = show.get('ids') or {}
            ids_ep = ep.get('ids') or {}
            first_aired = _date_only(r.get('first_aired') or ep.get('first_aired') or '')
            days_until = None
            if first_aired:
                try:
                    air_date = datetime.strptime(first_aired, '%Y-%m-%d').date()
                    days_until = (air_date - today).days
                except Exception:
                    days_until = None
            canonical = ids_show.get('imdb') or ('tmdb:%s' % ids_show.get('tmdb')) if ids_show.get('tmdb') else ''
            out.append({
                'canonical_id': canonical,
                'imdb_id': ids_show.get('imdb') or '',
                'tmdb_id': str(ids_show.get('tmdb') or ''),
                'tvdb_id': str(ids_show.get('tvdb') or ''),
                'title': show.get('title') or '',
                'year': show.get('year') or 0,
                'season': int(ep.get('season') or 0),
                'episode': int(ep.get('number') or 0),
                'episode_title': ep.get('title') or '',
                'first_aired': first_aired,
                'days_until': days_until,
                'overview': ep.get('overview') or '',
            })
        except Exception:
            continue
    # Group by date implicitly via sorted air_date asc.
    out.sort(key=lambda x: (x.get('first_aired') or '', x.get('title') or ''))
    return out


