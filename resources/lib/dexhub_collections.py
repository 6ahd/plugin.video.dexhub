# -*- coding: utf-8 -*-
"""Dex Hub's own collections in DexWorld's shape (v5.10.612).

"Picture & Disc" (k:dexhub-av), a collection like DexWorld's platforms: its two
lists are cover cards on the Home and open DexWorld's collection page:
  b:local-fel7      the films confirmed to carry Dolby Vision Profile 7 FEL (fel.py)
  b:local-fulldisc  the films a search found a whole UHD/Blu-ray disc of (disc_library.py)
dexworld.page() adds rows(); dexworld.row_page() asks page() for a local list.

The collection belongs on the DexWorld server, like Netflix (specs/dexworld-picture-disc.md):
when the site's page carries the kit (same id, lists b:dexhub-fel7 / b:dexhub-fulldisc with
TMDb titles, logos and the server's covers), merge() keeps the server's and only adds the
viewer's own full discs to it; until then the local kit above stands in.
"""
KIT = 'k:dexhub-av'
FEL = 'b:local-fel7'
DISC = 'b:local-fulldisc'
MEDIA = 'special://home/addons/plugin.video.dexhub/resources/media/fel/'
PER_PAGE = 40
FIRST = 14


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _entries(row_id):
    from . import fel
    if row_id == FEL:
        out = list(fel.titles())
        out.sort(key=lambda t: -(t.get('rating') or 0))
        return out
    if row_id == DISC:
        from . import disc_library
        return disc_library.titles()
    return []


def _item(entry):
    from . import fel
    imdb = str(entry.get('imdb') or '')
    return {'type': 'movie', 'imdb': imdb, 'tmdb': '', 'title': entry.get('title') or '',
            'year': entry.get('year') or '', 'rating': entry.get('rating') or 0,
            'poster': fel.art(imdb, 'poster'), 'backdrop': fel.art(imdb, 'background')}


def rows():
    """The kit and its two lists, as rows of DexWorld's page."""
    fel_title = _t('مجموعة FEL 7', 'FEL 7 Collection')
    disc_title = _t('الأقراص الكاملة', 'Full Disc')
    fel_desc = _t('أفلام فيها Dolby Vision Profile 7 بطبقة FEL الكاملة على أقراص UHD',
                  'Films with Dolby Vision Profile 7 and its full enhancement layer on UHD Blu-ray')
    disc_desc = _t('أفلام لقينا لها قرص UHD كامل (ISO أو BDMV) في مصادرك، تزيد كل ما تبحث',
                   'Films your sources have as a whole UHD disc (ISO or BDMV); it grows as you search')
    fel_items = _entries(FEL)
    disc_items = _entries(DISC)
    members = [{'id': FEL, 'title': fel_title, 'cover': MEDIA + 'fel_banner.jpg', 'ready': True, 'meta': fel_desc}]
    if disc_items:
        members.append({'id': DISC, 'title': disc_title, 'cover': MEDIA + 'disc_banner.jpg', 'ready': True,
                        'meta': disc_desc})
    kit = {'id': KIT, 'kind': 'kit', 'cat': '', 'cats': [], 'main': False, 'types': ['movie'],
           'title': _t('الصورة والأقراص', 'Picture & Disc'), 'desc': fel_desc, 'style': 'covers',
           'members': members, 'items': []}
    out = [kit,
           {'id': FEL, 'kind': 'block', 'cat': '', 'cats': [], 'main': False, 'types': ['movie'],
            'title': fel_title, 'desc': fel_desc, 'meta': fel_desc, 'cover': MEDIA + 'fel_banner.jpg',
            'logo': MEDIA + 'wide_edge.png', 'total': len(fel_items),
            'items': [_item(e) for e in fel_items[:FIRST]]}]
    if disc_items:
        out.append({'id': DISC, 'kind': 'block', 'cat': '', 'cats': [], 'main': False, 'types': ['movie'],
                    'title': disc_title, 'desc': disc_desc, 'meta': disc_desc, 'cover': MEDIA + 'disc_banner.jpg',
                    'logo': MEDIA + 'disc_wide.png', 'total': len(disc_items),
                    'items': [_item(e) for e in disc_items[:FIRST]]})
    return out


def row_page(row_id, number=1):
    """A local list's page, as dexworld.row_page answers."""
    entries = _entries(row_id)
    number = max(1, int(number or 1))
    pages = max(1, (len(entries) + PER_PAGE - 1) // PER_PAGE)
    chunk = entries[(number - 1) * PER_PAGE:number * PER_PAGE]
    title = _t('مجموعة FEL 7', 'FEL 7 Collection') if row_id == FEL else _t('الأقراص الكاملة', 'Full Disc')
    return {'items': [_item(e) for e in chunk], 'page': number, 'pages': pages, 'total': len(entries), 'title': title}


SPLIT_DISC = 'k:dexhub-fulldisc'     # the site's own Full Disc collection (EXTRAS_SPLIT_V1)
SPLIT_FEL = 'k:dexhub-fel7'


_LOGOS = (('dexhub-fel7-logo', 'wide_edge.png'), ('dexhub-fulldisc-logo', 'disc_wide.png'))


def _own_logo(row):
    """The site's FEL 7 / Full Disc badges answer 404 (25 Nov 2026): the same badges ship with
    Dex Hub, so those two are shown from here."""
    logo = str(row.get('logo') or '')
    for name, local in _LOGOS:
        if name in logo:
            row = dict(row)
            row['logo'] = MEDIA + local
            break
    return row


def merge(site_rows):
    """The site's rows with this collection. The site's two collections (FEL 7, Full Disc, each
    with rows inside) when it has them: the old Picture & Disc kit is left out and the viewer's
    own full discs join Full Disc as a list of their own. Else the site's Picture & Disc kit
    (the discs join it), else the local kit after the site's rows."""
    site = [_own_logo(r) for r in site_rows or [] if not str(r.get('id') or '').startswith('b:local-')]
    ids = set(str(r.get('id') or '') for r in site)
    split = SPLIT_DISC in ids and SPLIT_FEL in ids
    if split:
        site = [r for r in site if str(r.get('id') or '') != KIT]
        host = SPLIT_DISC
    elif KIT in ids:
        host = KIT
    else:
        return site + rows()
    mine = _entries(DISC)
    if not mine:
        return site
    title = _t('أقراصي الكاملة', 'My Full Discs')
    desc = _t('أفلام لقينا لها قرص UHD كامل في مصادرك', 'Films your own sources have as a whole UHD disc')
    block = {'id': DISC, 'kind': 'block', 'cat': '', 'cats': [], 'main': False, 'types': ['movie'],
             'title': title, 'desc': desc, 'meta': desc, 'cover': MEDIA + 'disc_banner.jpg',
             'logo': MEDIA + 'disc_wide.png', 'total': len(mine), 'items': [_item(e) for e in mine[:FIRST]]}
    out = []
    for r in site:
        if str(r.get('id') or '') == host:
            r = dict(r)
            r['members'] = list(r.get('members') or []) + [
                {'id': DISC, 'title': title, 'cover': MEDIA + 'disc_banner.jpg', 'ready': True, 'meta': desc}]
        out.append(r)
    return out + [block]


def is_local(row_id):
    return str(row_id or '').startswith('b:local-') or str(row_id or '') == KIT
