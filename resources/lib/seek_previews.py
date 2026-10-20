"""Ready-made seek thumbnails; no video decoding, Pillow or extra video stream."""
import bisect
import hashlib
import json
import math
import os
import re
import struct
import time
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

MAX_IMAGE = 16 * 1024 * 1024
CACHE_LIMIT = 64 * 1024 * 1024


class _Redirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        next_request = super().redirect_request(req, fp, code, msg, headers, newurl)
        if next_request and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            for key in ('X-api-key', 'X-emby-token', 'Authorization', 'Cookie'):
                next_request.remove_header(key)
        return next_request


def fetch(url, headers=None, limit=1024 * 1024):
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Unsupported preview URL')
    with build_opener(_Redirect()).open(Request(url, headers=headers or {}), timeout=5) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Preview exceeds size limit')
    return data


def seconds(text):
    parts = text.strip().replace(',', '.').split(':')
    if not 1 <= len(parts) <= 3:
        raise ValueError('Invalid timestamp')
    value = 0.0
    for part in parts:
        number = float(part)
        if not math.isfinite(number) or number < 0:
            raise ValueError('Invalid timestamp')
        value = value * 60 + number
    return value


def parse_vtt(text, base):
    cues = []
    for block in re.split(r'\n\s*\n', text.replace('\r', '')):
        lines = block.strip().splitlines()
        for i, line in enumerate(lines):
            if '-->' not in line or i + 1 >= len(lines):
                continue
            try:
                start, end = line.split('-->', 1)
                a, b = seconds(start), seconds(end.strip().split()[0])
                resource = lines[i + 1].strip()
                url, marker, fragment = resource.partition('#')
                url = urljoin(base, url)
                if urlsplit(url).scheme not in ('http', 'https') or not a < b:
                    continue
                crop = None
                if marker:
                    match = re.fullmatch(r'xywh=(\d+),(\d+),(\d+),(\d+)', fragment)
                    if not match:
                        continue
                    crop = tuple(map(int, match.groups()))
                    if min(crop[2:]) <= 0:
                        continue
                cues.append((a, b, url, crop))
            except (ValueError, IndexError):
                pass
            break
        if len(cues) >= 25000:
            break
    return sorted(cues, key=lambda c: c[0])


def image_size(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        size = struct.unpack('>II', data[16:24])
    elif data[:3] == b'GIF' and len(data) >= 10:
        size = struct.unpack('<HH', data[6:10])
    elif data[:2] == b'\xff\xd8':
        pos, size = 2, None
        while pos + 4 <= len(data):
            if data[pos] != 255:
                raise ValueError('Invalid JPEG marker')
            while pos < len(data) and data[pos] == 255:
                pos += 1
            marker = data[pos]
            pos += 1
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            length = struct.unpack('>H', data[pos:pos + 2])[0]
            if length < 2 or pos + length > len(data):
                raise ValueError('Invalid JPEG segment')
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack('>HH', data[pos + 3:pos + 7])
                size = (w, h)
                break
            pos += length
        if size is None:
            raise ValueError('JPEG size unavailable')
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        kind = data[12:16]
        if kind == b'VP8X' and len(data) >= 30:
            size = (int.from_bytes(data[24:27], 'little') + 1,
                    int.from_bytes(data[27:30], 'little') + 1)
        elif kind == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a' and len(data) >= 30:
            size = tuple(n & 0x3fff for n in struct.unpack('<HH', data[26:30]))
        elif kind == b'VP8L' and len(data) >= 25 and data[20] == 0x2f:
            bits = int.from_bytes(data[21:25], 'little')
            size = ((bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1)
        else:
            raise ValueError('Unsupported WebP header')
    else:
        raise ValueError('Unsupported preview image')
    if min(size) <= 0 or max(size) > 8192 or size[0] * size[1] > 32 * 1024 * 1024:
        raise ValueError('Preview dimensions out of bounds')
    return size


def geometry(size, crop=None):
    w, h = size
    x, y, tw, th = crop or (0, 0, w, h)
    if min(x, y) < 0 or min(tw, th) <= 0 or x + tw > w or y + th > h:
        raise ValueError('Tile outside sprite sheet')
    scale = min(320.0 / tw, 180.0 / th)
    return (round((320 - tw * scale) / 2 - x * scale),
            round((180 - th * scale) / 2 - y * scale),
            round(w * scale), round(h * scale))


def cached_image(url, headers, directory):
    os.makedirs(directory, exist_ok=True)
    name = hashlib.sha256((url + '\n' + json.dumps(headers, sort_keys=True)).encode()).hexdigest()
    path = next((os.path.join(directory, name + ext) for ext in ('.jpg', '.png', '.webp', '.gif')
                 if os.path.isfile(os.path.join(directory, name + ext))), '')
    if path:
        with open(path, 'rb') as handle:
            data = handle.read(MAX_IMAGE + 1)
        try:
            size = image_size(data)
            os.utime(path, None)
            return path, size
        except (ValueError, IndexError, struct.error):
            os.remove(path)
    data = fetch(url, headers, MAX_IMAGE)
    size = image_size(data)
    ext = '.png' if data.startswith(b'\x89PNG') else '.jpg' if data.startswith(b'\xff\xd8') else '.webp' if data.startswith(b'RIFF') else '.gif'
    path = os.path.join(directory, name + ext)
    # Keep disk use bounded. One preview worker writes at a time.
    files = [(os.path.getmtime(p), p, os.path.getsize(p))
             for p in (os.path.join(directory, n) for n in os.listdir(directory)) if os.path.isfile(p)]
    total = sum(row[2] for row in files)
    for _, old, length in sorted(files):
        if total + len(data) <= CACHE_LIMIT:
            break
        os.remove(old)
        total -= length
    temp = path + '.tmp'
    with open(temp, 'wb') as handle:
        handle.write(data)
    os.replace(temp, path)
    return path, size


class Track:
    def __init__(self, cues, headers=None):
        self.cues = cues
        self.starts = [c[0] for c in cues]
        self.headers = headers or {}

    def at(self, target):
        i = bisect.bisect_right(self.starts, target) - 1
        if i >= 0 and self.cues[i][0] <= target < self.cues[i][1]:
            return self.cues[i]
        return None

    def frame(self, target, directory):
        cue = self.at(target)
        if cue is None:
            return None
        path, size = cached_image(cue[2], self.headers, directory)
        return (path, geometry(size, cue[3]), target)


def server_context(ctx):
    """Recover ready-image access from a native context or a direct server URL.

    Only the actual media URL's known Emby/Jellyfin route is recognized. No
    guessing providers, remote redirects, account scanning or title search.
    """
    out = dict(ctx)
    if out.get('server_type') in ('emby', 'jellyfin', 'silo') and out.get('item_id'):
        return out
    raw, _, options = str(ctx.get('stream_url') or '').partition('|')
    parsed = urlsplit(raw)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return out
    match = re.fullmatch(r'(.*?)/(?:emby/)?(?:Videos/([^/]+)/(?:stream[^/]*|master\.m3u8)|Items/([^/]+)/Download)', parsed.path, re.I)
    if not match:
        return out
    query = {k.lower():v for k,v in parse_qsl(parsed.query)}
    headers = {k.lower():v for k,v in parse_qsl(options)}
    token = query.get('api_key') or query.get('x-emby-token') or headers.get('x-emby-token')
    if not token:
        return out
    backend = 'emby' if '/emby/' in parsed.path.lower() else 'jellyfin'
    base = parsed.scheme + '://' + parsed.netloc + match.group(1)
    out.update(server_type=backend, server_url=base, token=token,
               item_id=match.group(2) or match.group(3),
               media_source_id=query.get('mediasourceid') or '')
    return out


def server_track(ctx, duration):
    ctx = server_context(ctx)
    backend = str(ctx.get('server_type') or '').lower()
    if backend not in ('emby', 'jellyfin', 'silo') or not ctx.get('item_id'):
        return None
    from . import emby_client as E
    server = {'url': ctx.get('server_url'), 'token': ctx.get('token'), 'backend': backend}
    if not server['url'] or not server['token']:
        return None
    item_id = quote(str(ctx['item_id']), safe='')
    with E.bounded_timeout(5):
        data = E._api(server, '/Items/' + item_id, {'Fields': 'Chapters,Trickplay,MediaSources'}) or {}
    base = E._base(server).rstrip('/') + E._compat_prefix(server)
    headers = {'X-Emby-Token': server['token']}
    source = str(ctx.get('media_source_id') or '')
    # Jellyfin returns a map: media source -> thumbnail width -> grid info.
    all_info = data.get('Trickplay') or {}
    info = all_info.get(source) if source else all_info.get(str(data.get('Id') or ''))
    if not info and len(all_info) == 1 and not source:
        info = next(iter(all_info.values()))
    if isinstance(info, dict) and info:
        choices = sorted((v for v in info.values() if isinstance(v, dict)),
                         key=lambda v: abs(int(v.get('Width') or 0) - 320))
        for row in choices:
            try:
                w, h, cols, rows, count, interval = (int(row[k]) for k in
                    ('Width', 'Height', 'TileWidth', 'TileHeight', 'ThumbnailCount', 'Interval'))
                if min(w, h, cols, rows, count, interval) <= 0 or count > 25000:
                    continue
                params = ('?' + urlencode({'mediaSourceId': source})) if source else ''
                cues = []
                for n in range(count):
                    a = n * interval / 1000.0
                    if a >= duration:
                        break
                    tile, cell = divmod(n, cols * rows)
                    y, x = divmod(cell, cols)
                    url = '%s/Videos/%s/Trickplay/%s/%s.jpg%s' % (base, item_id, w, tile, params)
                    cues.append((a, min(duration, a + interval / 1000.0), url, (x * w, y * h, w, h)))
                if cues:
                    return Track(cues, headers)
            except (KeyError, TypeError, ValueError):
                continue
    chapters = []
    for i, row in enumerate(data.get('Chapters') or []):
        try:
            a = float(row['StartPositionTicks']) / 10000000.0
            if row.get('ImageTag') and 0 <= a < duration:
                url = '%s/Items/%s/Images/Chapter/%d?%s' % (base, item_id, i,
                      urlencode({'tag': row['ImageTag'], 'maxWidth': 320}))
                chapters.append((a, duration, url, None))
        except (KeyError, TypeError, ValueError):
            pass
    chapters.sort()
    return Track([(c[0], chapters[i + 1][0] if i + 1 < len(chapters) else duration, c[2], None)
                  for i, c in enumerate(chapters)], headers) if chapters else None


def seekr_track(playing, duration, key):
    if not key.strip():
        return None
    q = playing.get('q') or {}
    params = {'duration_ms': int(duration * 1000)}
    for field in ('tmdb', 'imdb'):
        if q.get(field):
            params[('show_' if playing.get('series') else '') + field + '_id'] = q[field]
    if len(params) == 1:
        return None
    if playing.get('series'):
        if playing.get('season') is None or playing.get('episode') is None:
            return None
        params.update(season=playing['season'], episode=playing['episode'])
    data = json.loads(fetch('https://api.seekr.tv/sprites?' + urlencode(params),
                            {'X-API-Key': key}).decode('utf-8'))
    url = data.get('vtt_url') or ''
    parsed = urlsplit(url)
    if parsed.scheme != 'https':
        return None
    # Signed VTT and image requests never receive the user's API key.
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k != 'st'] + [('st', '1')]
    url = urlunsplit(parsed._replace(query=urlencode(query)))
    cues = parse_vtt(fetch(url, limit=2 * 1024 * 1024).decode('utf-8-sig'), url)
    return Track(cues) if cues else None
