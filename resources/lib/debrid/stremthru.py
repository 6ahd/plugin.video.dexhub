# -*- coding: utf-8 -*-
"""What a Debrid service has cached, asked of StremThru (MIT, github.com/MunifTanjim/stremthru).

Real-Debrid and AllDebrid stopped telling apps which torrents they hold in
2024. StremThru answers from what its users' accounts found, for every
service: GET /v0/store/magnets/check?magnet=<hash,...>&sid=<imdb id> with the
service's name and the viewer's key in its two headers. The key goes to the
StremThru server for that request (the viewer allows it: Debrid ->
"Know what is ready before playing"); nothing else is sent.
"""
import urllib.parse

from . import accounts, net

DEFAULT_URL = 'https://stremthru.13377001.xyz'
STATUS = {'cached': 'cached', 'downloaded': 'cached'}


def base_url():
    return str(accounts.option('stremthru_url') or DEFAULT_URL).rstrip('/')


def check(service, token, hashes, sid='', timeout=12):
    """{hash: 'cached' | 'uncached'} for the hashes StremThru answered about."""
    out = {}
    hashes = [h.lower() for h in dict.fromkeys(hashes or []) if h]
    for start in range(0, len(hashes), 400):
        part = hashes[start:start + 400]
        query = {'magnet': ','.join(part)}
        if sid:
            query['sid'] = sid
        answer = net.get('%s/v0/store/magnets/check?%s' % (base_url(), urllib.parse.urlencode(query)),
                         headers={'X-StremThru-Store-Name': service,
                                  'X-StremThru-Store-Authorization': 'Bearer %s' % token},
                         timeout=timeout)
        data = answer.get('data') if isinstance(answer, dict) else None
        for item in (data or {}).get('items') or []:
            info_hash = str(item.get('hash') or '').lower()
            status = str(item.get('status') or '').lower()
            if info_hash and status and status != 'unknown':
                out[info_hash] = STATUS.get(status, 'uncached')
    return out
