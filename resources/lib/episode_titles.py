# -*- coding: utf-8 -*-
"""What an episode is called on its cards and in a season's list (v5.10.214).

Following and library: Episode names.
  'all'      the episode's own name everywhere; a source that only says
             "Episode 3" gets the real name from TMDb (default)
  'started'  names only on episodes already begun or watched, so the next
             one gives nothing away (the same choice as Episode pictures)
  'off'      numbers only: "S1:E3", "Episode 3"
"""
import re
import time

MODES = ('all', 'started', 'off')
_GENERIC_RE = re.compile(
    u'^\\s*(?:episode|ep\\.?|e|الحلقة|حلقة)'
    u'\\s*[.:#-]?\\s*\\d+\\s*$', re.I)
_MEMO = [0.0, 'all']


def mode():
    """The setting, read at most every three seconds."""
    now = time.time()
    if now - _MEMO[0] < 3.0:
        return _MEMO[1]
    value = 'all'
    try:
        import xbmcaddon
        value = (xbmcaddon.Addon('plugin.video.dexhub').getSetting('episode_titles') or 'all').strip().lower()
    except Exception:
        pass
    _MEMO[0], _MEMO[1] = now, value if value in MODES else 'all'
    return _MEMO[1]


def forget():
    _MEMO[0] = 0.0


def generic(name):
    """True for a name that is only the number ("Episode 3", "الحلقة 3", "E3")."""
    text = str(name or '').strip()
    return not text or bool(_GENERIC_RE.match(text)) or text.isdigit()


def wanted(started=True):
    """Whether this episode's name may be shown at all."""
    current = mode()
    return current == 'all' or (current == 'started' and started)


def pick(name, started=True, tmdb_id='', imdb_id='', season=0, episode=0, cached_only=False):
    """The name to show for an episode, '' for its number only.

    The source's own name wins; a missing or numbered one is asked of TMDb
    (one request per season, kept a month) unless ``cached_only``."""
    if not wanted(started):
        return ''
    name = str(name or '').strip()
    if name and not generic(name):
        return name
    if not (tmdb_id or imdb_id) or not episode:
        return ''
    try:
        from . import tmdb_direct
        return tmdb_direct.episode_name(tmdb_id=tmdb_id, imdb_id=imdb_id, season=season,
                                        episode=episode, cached_only=cached_only)
    except Exception:
        return ''


def list_label(tr, number, name, started=True):
    """A season list's line: "E3  The Name", or "Episode 3" without one."""
    number = int(number or 0)
    name = str(name or '').strip()
    if not wanted(started) or generic(name):
        return tr('الحلقة %d') % number
    return 'E%d  %s' % (number, name)


def show_ids(row):
    """(tmdb_id, imdb_id) of an episode row's SHOW. A row's own tmdb_id can be
    the episode's (v5.10.209), so only show_tmdb_id or a tmdb: canonical id
    count as the show's."""
    row = row or {}
    tmdb = str(row.get('show_tmdb_id') or '').strip()
    if row.get('native_backend') or row.get('native_server_id'):
        # a Plex, Emby, Jellyfin or Silo episode: its ids are the episode's own
        return (tmdb if tmdb.isdigit() else ''), ''
    cid = str(row.get('canonical_id') or '').strip()
    if not tmdb.isdigit():
        m = re.match(r'^tmdb[:_](\d+)', cid, re.I)
        tmdb = m.group(1) if m else ''
    m = re.search(r'(?i)\btt\d{5,12}\b', str(row.get('imdb_id') or '')) or re.search(r'(?i)\btt\d{5,12}\b', cid)
    return tmdb, (m.group(0).lower() if m else '')


def warm(keys, workers=6):
    """Ask TMDb for the seasons of these (tmdb_id, imdb_id, season) at once,
    so a listing that names many episodes waits for one round only."""
    keys = [k for k in dict.fromkeys(keys or []) if (k[0] or k[1]) and k[2] is not None]
    if not keys or mode() == 'off':
        return
    try:
        from . import tmdb_direct
        language = tmdb_direct._card_language()
        todo = [k for k in keys
                if tmdb_direct.season_episodes(k[0], k[1], k[2], True, language) is None][:12]
    except Exception:
        return
    if not todo:
        return
    english = not language or language.lower().startswith('en')

    def fetch(k):
        rows = tmdb_direct.season_episodes(k[0], k[1], k[2], False, language) or {}
        if not english and any(generic((v or {}).get('name')) for v in rows.values()):
            # TMDb often has no Arabic names: the English ones stand in
            tmdb_direct.season_episodes(k[0], k[1], k[2], False, 'en-US')
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as pool:
            list(pool.map(fetch, todo))
    except Exception:
        pass
