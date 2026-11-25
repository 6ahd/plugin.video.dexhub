# -*- coding: utf-8 -*-
"""The title page's sources as Flix shows them (v5.10.612, skin.dexhub 3.28.239).

The page's sources search (skinui/actions.warm_sources) hands every add-on's
answer here; the streams of one add-on at one quality share a card, best
first, as Flix's FlixSourcesRail draws them: the add-on's logo, "+N" when the
card holds more streams, a crown on the best one, the add-on's name, the
quality with the picture and the sound as pills, the size and the bitrate,
and a bolt when it is ready (debrid cache).

The cards wait in Home's dhs.tp.src.<page> (JSON) and the skin's row 8380
lists them through skin_src_rail; dhs.tp.rev.src makes Kodi ask again.
A card opens the sources page on its add-on (dexhub.src.group).
Off unless title_sources_rail is on.
"""
import json
import re
import time

import xbmc
import xbmcgui

KEY = 'title_sources_rail'
LIMIT = 16
TITLE_FEL = [False]    # v5.10.612: the searched title is on the FEL list (set by warm_sources)

_RES = ((re.compile(r'(?<![0-9])(2160p|4k|uhd)(?![a-z0-9])', re.I), '4K', 5),
        (re.compile(r'(?<![0-9])1440p', re.I), '1440p', 4),
        (re.compile(r'(?<![0-9])1080[pi]?(?![0-9])', re.I), '1080p', 3),
        (re.compile(r'(?<![0-9])720p', re.I), '720p', 2),
        (re.compile(r'(?<![0-9])(480p|576p|sd|dvdrip)(?![a-z0-9])', re.I), 'SD', 1))
_DYN = ((re.compile(r'(?<![a-z])(dv|dovi|dolby[ ._-]?vision)(?![a-z])', re.I), 'Dolby Vision', 4),
        (re.compile(r'hdr10\+|hdr10plus', re.I), 'HDR10+', 3),
        (re.compile(r'hdr10(?!\+)', re.I), 'HDR10', 2),
        (re.compile(r'(?<![a-z])hdr(?![a-z0-9])', re.I), 'HDR', 2),
        (re.compile(r'(?<![a-z])hlg(?![a-z])', re.I), 'HLG', 1))
_AUD = ((re.compile(r'atmos', re.I), 'Atmos'),
        (re.compile(r'truehd', re.I), 'TrueHD'),
        (re.compile(r'dts[ ._-]?x(?![a-z])', re.I), 'DTS:X'),
        (re.compile(r'dts[ ._-]?hd', re.I), 'DTS-HD'),
        (re.compile(r'(?<![a-z])dts(?![a-z])', re.I), 'DTS'),
        (re.compile(r'(dd\+|ddp|e[ ._-]?ac[ ._-]?3)', re.I), 'DD+'),
        (re.compile(r'(?<![a-z])(dd|ac[ ._-]?3)(?![a-z+])', re.I), 'DD'),
        (re.compile(r'(?<![a-z])aac(?![a-z])', re.I), 'AAC'))
_CH = re.compile(r'(?<![0-9])([2-7])[ .]([01])(?:[ .]([24]))?(?![0-9])')
_SIZE = re.compile(r'(\d+(?:[.,]\d+)?)\s*(TB|GB|MB)(?![a-z])', re.I)
_CACHED = re.compile(r'(\[(?:RD|AD|PM|TB|DL|ED|OC|PKP)\+\]|⚡|\+\s*cached|instant)', re.I)
_CAM = re.compile(r'(?<![a-z])(cam|hdcam|telesync|hdts|(?<![a-z])ts(?![a-z]))(?![a-z])', re.I)


def enabled():
    try:
        from .ui_preferences import value
        return value(KEY) == 'true'
    except Exception:
        return False


def _text(stream):
    hints = stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'), dict) else {}
    return ' '.join(str(x or '') for x in (stream.get('name'), stream.get('title'), stream.get('description'),
                                           hints.get('filename'))).replace('\n', ' ')


def _size(stream, text):
    hints = stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'), dict) else {}
    try:
        size = int(hints.get('videoSize') or stream.get('size') or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        found = _SIZE.search(text)
        if found:
            number = float(found.group(1).replace(',', '.'))
            size = int(number * {'TB': 1024 ** 4, 'GB': 1024 ** 3, 'MB': 1024 ** 2}[found.group(2).upper()])
    return size


def _size_text(size):
    if size <= 0:
        return ''
    if size >= 1024 ** 3:
        return '%.1f GB' % (size / float(1024 ** 3))
    return '%d MB' % round(size / float(1024 ** 2))


def _logo(provider):
    manifest = provider.get('manifest') if isinstance(provider.get('manifest'), dict) else {}
    logo = str(manifest.get('logo') or manifest.get('icon') or provider.get('logo') or '').strip()
    if logo.startswith(('http://', 'https://', 'special://')):
        return logo
    name = str(provider.get('name') or provider.get('id') or '').lower()
    for word, image in (('plex', 'provider_plex.png'), ('emby', 'provider_emby.png'),
                        ('jellyfin', 'provider_jellyfin.png'), ('silo', 'provider_silo.png'),
                        ('aiostreams', 'provider_aiostreams.png')):
        if word in name:
            return 'special://home/addons/plugin.video.dexhub/resources/media/' + image
    return 'special://home/addons/plugin.video.dexhub/resources/media/provider.png'


def describe(stream):
    text = _text(stream)
    quality, rank = 'Other', 0
    for pattern, label, value in _RES:
        if pattern.search(text):
            quality, rank = label, value
            break
    dyn, dyn_rank = '', 0
    for pattern, label, value in _DYN:
        if pattern.search(text):
            dyn, dyn_rank = label, value
            break
    audio = next((label for pattern, label in _AUD if pattern.search(text)), '')
    channels = _CH.search(text)
    chan = ('%s.%s' % (channels.group(1), channels.group(2)) + ('.%s' % channels.group(3) if channels.group(3) else '')
            ) if channels else ''
    size = _size(stream, text)
    try:
        from . import fel
        layer = fel.layer(text, TITLE_FEL[0])
    except Exception:
        layer = ''
    if layer == 'FEL':
        dyn_rank = 6          # a FEL copy leads its card
    try:
        from .disc_library import DISC_RE
        disc = bool(DISC_RE.search(text))
    except Exception:
        disc = False
    return {'q': quality, 'rank': rank, 'dyn': dyn, 'dyn_rank': dyn_rank, 'fel': layer, 'disc': disc,
            'aud': ' '.join(x for x in (audio, chan) if x), 'size': size,
            'cached': bool(_CACHED.search(text)), 'cam': bool(_CAM.search(text))}


def groups(found, limit=LIMIT):
    """The cards: one per add-on and quality, the best stream leading."""
    cards = {}
    order = []
    for provider, streams in found or ():
        provider = provider if isinstance(provider, dict) else {}
        name = str(provider.get('name') or provider.get('id') or '').strip()
        if not name:
            continue
        for stream in streams or ():
            if not isinstance(stream, dict):
                continue
            info = describe(stream)
            if info['cam']:
                continue                        # as Flix: no CAM copies
            key = (name, info['q'])
            card = cards.get(key)
            if card is None:
                card = cards[key] = {'name': name, 'logo': _logo(provider), 'n': 0, 'lead': info}
                order.append(key)
            card['n'] += 1
            card['disc'] = card.get('disc') or info.get('disc')
            lead = card['lead']
            if (info['dyn_rank'], info['cached'], info['size']) > (lead['dyn_rank'], lead['cached'], lead['size']):
                card['lead'] = info
    out = []
    for key in order:
        card = cards[key]
        lead = card['lead']
        out.append({'name': card['name'], 'logo': card['logo'], 'n': card['n'], 'q': lead['q'], 'rank': lead['rank'],
                    'dyn': lead['dyn'], 'dyn_rank': lead['dyn_rank'], 'aud': lead['aud'],
                    'size': _size_text(lead['size']), 'bytes': lead['size'], 'cached': lead['cached'],
                    'fel': lead.get('fel') or '', 'disc': bool(card.get('disc'))})
    out.sort(key=lambda c: (-c['rank'], -c['dyn_rank'], not c['cached'], -c['bytes']))
    if out:
        out[0]['best'] = True
    return out[:limit]


def store(page, cards):
    """The page's cards for the skin's row; the row asks again."""
    if not page:
        return
    home = xbmcgui.Window(10000)
    home.setProperty('dhs.tp.src.%s' % page, json.dumps(cards or []))
    mark(page)


def mark(page):
    """The skin shows the row (and so Kodi fills it) for the page named in
    dhs.tp.src.has; a hidden list is never filled."""
    home = xbmcgui.Window(10000)
    raw = home.getProperty('dhs.tp.src.%s' % page) if page else ''
    home.setProperty('dhs.tp.src.has', page if raw and raw != '[]' else '')
    home.setProperty('dhs.tp.rev.src', '%d' % (time.time() * 1000))


def listing(params, handle):
    """skin_src_rail: the open page's cards."""
    from .skinui import common as C
    from .skinui import items as I
    home = xbmcgui.Window(10000)
    page = home.getProperty('dhs.tp.id')
    try:
        cards = json.loads(home.getProperty('dhs.tp.src.%s' % page) or '[]') if page else []
    except ValueError:
        cards = []
    q = home.getProperty('dhs.tp.q')
    out = []
    for card in cards:
        target = '%s?action=skin_sources&%s' % (C.BASE, q)
        try:
            from urllib.parse import urlencode
            target += '&' + urlencode([('grp', card.get('name') or ''), ('n', str(card.get('n') or 1))])
        except Exception:
            pass
        more = int(card.get('n') or 1) - 1
        out.append({'label': card.get('name') or '', 'label2': card.get('q') or '',
                    'art': {'icon': card.get('logo') or '', 'thumb': card.get('logo') or ''},
                    'props': {'dhs.click': 'run', 'dhs.target': target, 'dhs.tp.owner': page,
                              'src.q': card.get('q') or '', 'src.dyn': card.get('dyn') or '',
                              'src.aud': card.get('aud') or '', 'src.size': card.get('size') or '',
                              'src.more': ('+%d' % more) if more > 0 else '',
                              'src.best': 'true' if card.get('best') else '',
                              'src.cached': 'true' if card.get('cached') else '',
                              'src.fel': card.get('fel') or ''}})
    I.directory(handle, out, content='videos')
    return None


def want_group(name, count=0):
    """The sources page opens on this add-on (source_browser reads it once),
    as the Dex Glass page over the title page; a card of one copy plays it."""
    if name:
        home = xbmcgui.Window(10000)
        home.setProperty('dexhub.src.group', name)
        home.setProperty('dexhub.src.group.at', '%d' % int(time.time()))   # the search waits for it (fresh)
        home.setProperty('dexhub.src.sheet', '1' if page_style() == 'glass' else '')
        home.setProperty('dexhub.src.single', '1' if str(count) == '1' else '')
        log('a card opens the sources of %s (%s cop%s)' % (name, count or '?', 'y' if str(count) == '1' else 'ies'))


def take_sheet():
    home = xbmcgui.Window(10000)
    sheet = home.getProperty('dexhub.src.sheet') == '1'
    if sheet:
        home.clearProperty('dexhub.src.sheet')
    return sheet


def page_style():
    """What a card opens: 'glass' (Dex Glass over the title page) or 'theme'
    (the sources page of the chosen theme, as before)."""
    try:
        from .ui_preferences import value
        return value('title_sources_page')
    except Exception:
        return 'glass'


def take_group():
    home = xbmcgui.Window(10000)
    name = home.getProperty('dexhub.src.group')
    if name:
        home.clearProperty('dexhub.src.group')
    return name


def take_single():
    home = xbmcgui.Window(10000)
    single = home.getProperty('dexhub.src.single') == '1'
    home.clearProperty('dexhub.src.single')
    return single


def log(message):
    xbmc.log('[DexHub] title sources: %s' % message, xbmc.LOGINFO)
