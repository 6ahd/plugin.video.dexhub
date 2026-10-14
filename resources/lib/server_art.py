"""Jellyfin/Emby image ownership, built from DTOs without network lookups."""
from urllib.parse import quote, urlencode


def listing_content(items, fallback='videos'):
    """Describe the actual rows, without fetching any extra metadata."""
    kinds = {item.get('media_type') for item in items}
    if len(kinds) == 1:
        return {'movie':'movies', 'show':'tvshows', 'season':'seasons',
                'episode':'episodes'}.get(next(iter(kinds)), fallback)
    return fallback


def artwork(item, server, prefix=''):
    tags = item.get('ImageTags') or {}
    iid = str(item.get('Id') or '')
    series = str(item.get('SeriesId') or '')
    parent = str(item.get('ParentPrimaryImageItemId') or series)
    parent_tag = item.get('ParentPrimaryImageTag') or item.get('SeriesPrimaryImageTag') or ''
    def url(owner, kind, tag='', width=600):
        if not owner:
            return ''
        params = {'maxWidth':width, 'quality':90, 'api_key':server.get('token') or ''}
        if tag:
            params['tag'] = tag
        return '%s%s/Items/%s/Images/%s?%s' % (str(server.get('url') or '').rstrip('/'),
            prefix, quote(str(owner), safe=''), kind, urlencode(params))
    own = url(iid, 'Primary', tags['Primary']) if tags.get('Primary') else ''
    inherited = url(parent, 'Primary', parent_tag) if parent and parent_tag else ''
    poster = (inherited or own) if item.get('Type') == 'Episode' else (own or inherited)
    backs = item.get('BackdropImageTags') or []
    parent_backs = item.get('ParentBackdropImageTags') or []
    fanart = url(iid, 'Backdrop/0', backs[0], 1280) if backs else ''
    if not fanart and parent_backs:
        fanart = url(item.get('ParentBackdropItemId') or series, 'Backdrop/0', parent_backs[0], 1280)
    logo = url(iid, 'Logo', tags['Logo'], 800) if tags.get('Logo') else ''
    if not logo and item.get('ParentLogoImageTag'):
        logo = url(item.get('ParentLogoItemId') or series, 'Logo', item['ParentLogoImageTag'], 800)
    return {k:v for k,v in {'poster':poster, 'thumb':own or poster, 'icon':poster or own,
        'fanart':fanart, 'landscape':fanart or own, 'clearlogo':logo,
        'tvshow.poster':inherited or poster, 'tvshow.clearlogo':logo}.items() if v}
