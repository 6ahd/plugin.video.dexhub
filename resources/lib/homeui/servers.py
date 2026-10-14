# -*- coding: utf-8 -*-
"""The Servers page of the Dex Hub Home (v5.10.103): Plex, Emby, Jellyfin, Silo.

v5.10.104: one server at a time, chosen in the page's tab bar (like the
Home's own tabs). The server's rows: what is being watched on it, what was
added to it lately (Plex), one row of posters per library, Silo's own Home
sections, and its libraries as cards. A library card opens the library in
the Home's grid, and a library or any of these rows can be added to the Home
as a row of its own (the same row spec the layout editor's "Add a row"
saves).

Everything is read from the classic Dex Hub routes through the capture
layer, so sorting, artwork and playback stay exactly as on the classic pages.
Nothing here touches the GUI.
"""
import hashlib
import json
import os
import re

from . import rows as R

# service, brand, client attribute of the plugin module
SERVICES = (
    ('plex', 'Plex', 'plex_client'),
    ('emby', 'Emby', 'emby_client'),
    ('jellyfin', 'Jellyfin', 'jellyfin_client'),
    ('silo', 'Silo', 'silo_client'),
)
BRANDS = dict((service, brand) for service, brand, _client in SERVICES)
# routes that list what is being watched: landscape cards with progress
PROGRESS_ACTIONS = frozenset(('plex_continue', 'emby_continue', 'jellyfin_continue'))
# menu entries that are not a library: search, account and settings actions,
# and the continue listings (they have their own row)
_NOT_LIBRARY = re.compile(
    r'(search|login|logout|refresh|sort|settings|setup|toggle|pair|qr|profile|'
    r'_continue$|_servers$|_menu$|silo_home$|play)', re.I)
_TAGS = re.compile(r'\[/?(?:COLOR|B|I|UPPERCASE|LOWERCASE|CAPITALIZE|LIGHT)[^\]]*\]', re.I)


def clean_label(text):
    return ' '.join(_TAGS.sub('', str(text or '')).split())


def _signed_in(api, client):
    try:
        return bool(getattr(api, client).is_signed_in())
    except Exception:
        return False


def linked_services(api):
    """The brands of the linked media servers, in page order."""
    return [brand for service, brand, client in SERVICES if _signed_in(api, client)]


def linked_servers(api, plex_budget=4.0):
    """[{service, brand, id, name}] for every linked server.

    Plex lists every server of the account (its discovery is memoised and
    bounded, see plugin._plex_servers_budgeted); Emby, Jellyfin and Silo
    serve one server per account, as their classic pages do.
    """
    out = []
    for service, brand, client in SERVICES:
        if not _signed_in(api, client):
            continue
        if service == 'plex':
            try:
                servers = api._plex_servers_budgeted(plex_budget) or []
            except Exception:
                servers = []
        else:
            try:
                servers = (getattr(api, client).servers() or [])[:1]
            except Exception:
                servers = []
        for server in servers:
            if not isinstance(server, dict):
                continue
            sid = str(server.get('id') or '')
            if service == 'plex' and not sid:
                continue
            out.append({'service': service, 'brand': brand, 'id': sid,
                        'name': clean_label(server.get('name')) or brand})
    # v5.10.168: the first server of each service first, then the account's
    # other Plex servers. A Plex account with several (shared) servers filled
    # the chips (All plus six) and the twenty rows of All before Emby,
    # Jellyfin or Silo came, so a linked Silo did not show at all.
    return ordered(out)


def merge_known(found, known):
    """v5.10.180: servers a slow answer left out this time (Plex's account
    list past its budget, a server waking up) stay from the last good list:
    a service that answered with nothing keeps the servers it had."""
    have = set(s.get('service') for s in found or [])
    keys = set(server_key(s) for s in found or [])
    out = list(found or [])
    for server in known or []:
        if not isinstance(server, dict) or server.get('service') in have:
            continue
        if server_key(server) in keys:
            continue
        out.append(dict(server))
    return out


def ordered(servers):
    """The first server of each service, then the others, in their order."""
    seen, first, rest = set(), [], []
    for server in servers or []:
        (rest if server.get('service') in seen else first).append(server)
        seen.add(server.get('service'))
    return first + rest


def server_key(server):
    """A server's tab: its service and id (one Emby, Jellyfin or Silo per account)."""
    return '%s:%s' % (server['service'], server.get('id') or server['service'])


def tabs(found, media_root):
    """The tab bar of the Servers page: every linked server with its brand's icon."""
    out = []
    for server in found:
        out.append({'key': server_key(server), 'label': server['name'], 'hint': server['brand'],
                    'icon': os.path.join(media_root, 'provider_%s.png' % server['service'])})
    return out


def library_key(params):
    """A short stable key for a library row."""
    params = dict(params or {})
    for name in ('key', 'library_id', 'parent_id', 'id'):
        if params.get(name):
            return re.sub(r'[^A-Za-z0-9]+', '_', str(params[name]))[:40]
    blob = json.dumps(params, sort_keys=True)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:12]


def menu_params(server):
    """The classic route that lists a server's libraries."""
    service = server['service']
    if service == 'plex':
        return {'action': 'plex_server', 'server_id': server['id']}
    return {'action': '%s_menu' % service}


def continue_params(server):
    service = server['service']
    if service == 'plex':
        return {'action': 'plex_continue', 'server_id': server['id']}
    if service in ('emby', 'jellyfin'):
        return {'action': '%s_continue' % service}
    return None


def recent_params(server, title):
    if server['service'] == 'plex':
        # Plex's own "recently added" across the server's libraries
        return {'action': 'plex_children', 'server_id': server['id'],
                'key': '/library/recentlyAdded', 'title': title}
    return None


def library_target(params):
    """What a library card opens: the titles themselves, not another menu.

    A Silo library is a menu of three doors (All, Recommended, Collections);
    its card opens All, the way Silo's own library page starts.
    """
    params = dict(params or {})
    params.pop('start', None)
    params.pop('offset', None)
    if params.get('action') == 'silo_library':
        return {'action': 'silo_catalog', 'library_id': params.get('library_id') or '',
                'media_type': params.get('media_type') or '', 'title': params.get('title') or ''}
    return params


def library_tiles(entries, server, media_path, fanart=''):
    """Library cards from a server's captured menu listing."""
    entries, _more = R.split_more(entries)
    service, brand = server['service'], server['brand']
    card = media_path('lib_%s.jpg' % service)
    tiles, seen = [], set()
    for entry in entries:
        path = entry.get('path') or ''
        if not entry.get('folder') or not R.is_dexhub_path(path):
            continue
        params = R.params_of(path)
        action = params.get('action') or ''
        if not action or _NOT_LIBRARY.search(action):
            continue
        target = library_target(params)
        key = tuple(sorted(target.items()))
        if key in seen:
            continue
        seen.add(key)
        title = clean_label(params.get('title') or entry.get('label'))
        if service in ('emby', 'jellyfin') and ' • ' in title and not params.get('title'):
            title = title.split(' • ')[0]
        art = entry.get('art') or {}
        backdrop = R._clean_art(art.get('fanart') or art.get('landscape') or '') or fanart
        tiles.append({
            'kind': 'library', 'title': title, 'label': title,
            'hint': server['name'], 'shape': 'landscape',
            'poster': card, 'landscape': card, 'fanart': backdrop,
            'params': target, 'path': path, 'folder': True,
            'service': service, 'brand': brand, 'server': server['name'],
            'plot': clean_label((entry.get('info') or {}).get('plot')),
        })
    return tiles


def section_rows(entries):
    """(title, params) of Silo Home sections from the captured silo_home listing."""
    entries, _more = R.split_more(entries)
    out = []
    for entry in entries:
        path = entry.get('path') or ''
        if not R.is_dexhub_path(path):
            continue
        params = R.params_of(path)
        if params.get('action') != 'silo_section' or not params.get('section_id'):
            continue
        params.pop('offset', None)
        out.append((clean_label(params.get('title') or entry.get('label')), params))
    return out


_PICKS = (
    ('continue', ('continue', 'resume', 'watching', 'in progress', '\u0645\u062a\u0627\u0628\u0639\u0629',
                  '\u0643\u0645\u0644', '\u0643\u0645\u0651\u0644', '\u0623\u0643\u0645\u0644')),
    ('next_up', ('next up', 'up next', '\u0627\u0644\u062a\u0627\u0644\u064a')),
    ('recent', ('recent', 'added', 'latest', 'new ', 'newest', '\u0623\u0636\u064a\u0641', '\u0627\u0636\u064a\u0641',
                '\u062c\u062f\u064a\u062f', '\u0627\u0644\u0623\u062d\u062f\u062b')),
)
# v5.10.198: Silo's own section types (its home layout names each row's kind)
_PICK_TYPES = {'continue_watching': 'continue', 'next_up': 'next_up', 'recently_added': 'recent'}
# the titles of the picked rows, and whether a row shows progress
PICK_TITLES = {'continue': ('\u0643\u0645\u0651\u0644 \u0627\u0644\u0645\u0634\u0627\u0647\u062f\u0629', 'Continue watching'),
               'next_up': ('\u0627\u0644\u062a\u0627\u0644\u064a', 'Next up'),
               'recent': ('\u0623\u0636\u064a\u0641 \u0645\u0624\u062e\u0631\u0627\u064b', 'Recently added')}


def picks(sections):
    """v5.10.168: Silo's own Home sections that stand for Continue watching,
    Next up (v5.10.198, when Silo shows it as a row of its own) and Recently
    added, [(kind, title, params)] (the All chip shows only these kinds for
    every server, to stay light)."""
    out, used = [], set()
    for kind, words in _PICKS:
        for title, params in sections or []:
            sid = str((params or {}).get('section_id') or '')
            if sid in used:
                continue
            stype = str((params or {}).get('section_type') or '')
            if stype:
                hit = _PICK_TYPES.get(stype) == kind
            else:
                low = ' %s ' % (title or '').lower()
                hit = any(w in low for w in words) or kind in sid.lower() or \
                    (kind == 'next_up' and 'next-up' in sid.lower())
            if hit:
                out.append((kind, title, params))
                used.add(sid)
                break
    return out


# v5.10.198: Silo's rows that lead its page (what is being watched, what is
# next, what was added), before its libraries
LEAD_SECTIONS = ('continue_watching', 'next_up', 'recently_added')


def silo_collection_rows(server):
    """v5.10.198: a Silo server's collections as a row of posters (the
    profile's own are among its libraries already, as Silo's menu lists
    them): [(key, (Arabic title, English title), params)]."""
    if (server or {}).get('service') != 'silo':
        return []
    return [('collections', ('\u0627\u0644\u0645\u062c\u0645\u0648\u0639\u0627\u062a', 'Collections'),
             {'action': 'silo_server_collections'})]


def row_spec(title, server, params, progress=False, library=False):
    """A layout custom row (homeui.layout) for a server listing."""
    brand = server.get('brand') or BRANDS.get(server.get('service'), '')
    return {
        'type': 'route', 'label': title, 'source': server.get('service') or '',
        'source_label': '%s  •  %s' % (brand, server.get('name') or brand),
        'subtitle': '%s  •  %s' % (brand, server.get('name') or brand),
        'params': dict(params or {}),
        'shape': 'landscape' if progress else '',
        'server_row': 'library' if library else ('progress' if progress else 'listing'),
    }


def is_progress(params):
    return (params or {}).get('action') in PROGRESS_ACTIONS
