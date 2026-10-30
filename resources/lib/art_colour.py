# -*- coding: utf-8 -*-
"""v5.10.448: a title's colour without an image library (Kodi on the Apple
TV and the Mac has no PIL). A JPEG's 8x8 blocks each start with their mean
(the DC coefficient), so the picture's colours come from the entropy-coded
data alone, with no inverse DCT: baseline pictures are read through once,
progressive ones only to the end of their first (DC) scans. A small TMDb
size (w300) is asked for, which is about 200 blocks of colour.

colour(url) -> {'accent': 'FFrrggbb', 'tone': 'FFrrggbb', 'glow': 'FFrrggbb'}
or None (no picture, not a JPEG, no telling colour); kept per address."""
import colorsys
import threading

_LOCK = threading.Lock()
_CACHE = {}
_CACHE_MAX = 300
MAX_BASELINE = 700 * 1024     # pixels: a larger baseline picture costs too much in Python


class _Bits(object):
    """The entropy-coded bytes of a scan, unstuffed, read MSB first."""

    def __init__(self, data, pos):
        self.data, self.pos, self.acc, self.n = data, pos, 0, 0
        self.marker = None

    def _byte(self):
        data, pos = self.data, self.pos
        if self.marker is not None or pos >= len(data):
            return 0
        b = data[pos]
        if b == 0xFF:
            nxt = data[pos + 1] if pos + 1 < len(data) else 0
            if nxt == 0x00:
                self.pos = pos + 2
                return 0xFF
            self.marker = nxt          # a marker ends the data (or a restart)
            return 0
        self.pos = pos + 1
        return b

    def bit(self):
        if not self.n:
            self.acc, self.n = self._byte(), 8
        self.n -= 1
        return (self.acc >> self.n) & 1

    def bits(self, count):
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def restart(self):
        """At an RSTn: drop the partial byte and step over the marker."""
        self.n = 0
        if self.marker is not None and 0xD0 <= self.marker <= 0xD7:
            self.pos += 2
            self.marker = None
            return True
        # (some encoders put the marker after the bits we already took)
        data = self.data
        while self.pos + 1 < len(data) and data[self.pos] == 0xFF and data[self.pos + 1] == 0xFF:
            self.pos += 1
        if self.pos + 1 < len(data) and data[self.pos] == 0xFF and 0xD0 <= data[self.pos + 1] <= 0xD7:
            self.pos += 2
            return True
        return False


def _huffman(counts, symbols):
    """{(length, code): symbol} for a DHT table."""
    table, code, k = {}, 0, 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[(length, code)] = symbols[k]
            code += 1
            k += 1
        code <<= 1
    return table


def _decode(bits, table):
    code = 0
    for length in range(1, 17):
        code = (code << 1) | bits.bit()
        symbol = table.get((length, code))
        if symbol is not None:
            return symbol
    raise ValueError('bad Huffman code')


def _extend(value, size):
    return value - (1 << size) + 1 if size and value < (1 << (size - 1)) else value


def _skip_scan(data, pos):
    """The position of the marker after a scan's entropy-coded data."""
    end = len(data)
    while pos + 1 < end:
        if data[pos] == 0xFF and data[pos + 1] not in (0x00, 0xFF) and not 0xD0 <= data[pos + 1] <= 0xD7:
            return pos
        pos += 1
    return end


def block_means(data):
    """[(r, g, b)] of the picture's blocks (one per MCU), or None."""
    if not data or data[:2] != b'\xff\xd8':
        return None
    pos, end = 2, len(data)
    qt, dc_tables, ac_tables = {}, {}, {}
    frame = None
    restart = 0
    dc = {}            # component id -> {(bx, by): DC}
    done = set()       # components whose DC is complete
    while pos + 4 <= end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            pos += 2 if marker != 0xFF else 1
            continue
        if marker == 0xD9:
            break
        length = (data[pos + 2] << 8) | data[pos + 3]
        seg = data[pos + 4:pos + 2 + length]
        pos += 2 + length
        if marker == 0xDB:                                  # DQT
            i = 0
            while i < len(seg):
                precision, tid = seg[i] >> 4, seg[i] & 15
                qt[tid] = (seg[i + 1] << 8 | seg[i + 2]) if precision else seg[i + 1]
                i += 1 + 64 * (2 if precision else 1)
        elif marker == 0xC4:                                # DHT
            i = 0
            while i < len(seg):
                cls, tid = seg[i] >> 4, seg[i] & 15
                counts = list(seg[i + 1:i + 17])
                total = sum(counts)
                table = _huffman(counts, list(seg[i + 17:i + 17 + total]))
                (ac_tables if cls else dc_tables)[tid] = table
                i += 17 + total
        elif marker == 0xDD:                                # DRI
            restart = (seg[0] << 8) | seg[1]
        elif marker in (0xC0, 0xC1, 0xC2):                  # SOF baseline / extended / progressive
            height, width = (seg[1] << 8) | seg[2], (seg[3] << 8) | seg[4]
            comps = []
            for k in range(seg[5]):
                cid, hv, tq = seg[6 + 3 * k], seg[7 + 3 * k], seg[8 + 3 * k]
                comps.append({'id': cid, 'h': hv >> 4, 'v': hv & 15, 'tq': tq})
            hmax = max(c['h'] for c in comps)
            vmax = max(c['v'] for c in comps)
            frame = {'w': width, 'h': height, 'comps': comps, 'hmax': hmax, 'vmax': vmax,
                     'progressive': marker == 0xC2,
                     'mx': (width + 8 * hmax - 1) // (8 * hmax), 'my': (height + 8 * vmax - 1) // (8 * vmax)}
            if not frame['progressive'] and width * height > MAX_BASELINE:
                return None
            for c in comps:
                dc[c['id']] = {}
        elif 0xC3 <= marker <= 0xCF:
            return None                                     # lossless / arithmetic: not read
        elif marker == 0xDA:                                # SOS
            if frame is None:
                return None
            ns = seg[0]
            scan = []
            for k in range(ns):
                cid, tables = seg[1 + 2 * k], seg[2 + 2 * k]
                comp = next(c for c in frame['comps'] if c['id'] == cid)
                scan.append((comp, tables >> 4, tables & 15))
            ss, se, ah_al = seg[1 + 2 * ns], seg[2 + 2 * ns], seg[3 + 2 * ns]
            al = ah_al & 15
            first_dc = ss == 0 and (ah_al >> 4) == 0
            if frame['progressive'] and not first_dc:
                pos = _skip_scan(data, pos)
                continue
            _read_scan(data, pos, frame, scan, dc_tables, ac_tables, restart, dc,
                       baseline=not frame['progressive'], se=se, al=al)
            for comp, _d, _a in scan:
                done.add(comp['id'])
            pos = _skip_scan(data, pos)
            if all(c['id'] in done for c in frame['comps']):
                break
    if frame is None or not all(c['id'] in done for c in frame['comps']):
        return None
    return _mcu_colours(frame, dc, qt)


def _read_scan(data, pos, frame, scan, dc_tables, ac_tables, restart, dc, baseline, se, al):
    bits = _Bits(data, pos)
    pred = dict((comp['id'], 0) for comp, _d, _a in scan)
    if len(scan) == 1:
        comp = scan[0][0]
        cw = (frame['w'] * comp['h'] + frame['hmax'] - 1) // frame['hmax']
        ch = (frame['h'] * comp['v'] + frame['vmax'] - 1) // frame['vmax']
        units = [((bx, by),) for by in range((ch + 7) // 8) for bx in range((cw + 7) // 8)]
        layout = [(scan[0], units)]
    else:
        units = []
        for my in range(frame['my']):
            for mx in range(frame['mx']):
                mcu = []
                for entry in scan:
                    comp = entry[0]
                    for v in range(comp['v']):
                        for h in range(comp['h']):
                            mcu.append((entry, (mx * comp['h'] + h, my * comp['v'] + v)))
                units.append(mcu)
        layout = None
    count = 0
    sequence = units if layout is None else [[(layout[0][0], u[0])] for u in layout[0][1]]
    for mcu in sequence:
        if restart and count and count % restart == 0:
            bits.restart()
            for key in pred:
                pred[key] = 0
        count += 1
        for (comp, td, ta), at in mcu:
            size = _decode(bits, dc_tables[td])
            diff = _extend(bits.bits(size), size) if size else 0
            pred[comp['id']] += diff
            dc[comp['id']][at] = pred[comp['id']] << al
            if baseline:
                table, k = ac_tables[ta], 1
                while k <= se:
                    rs = _decode(bits, table)
                    run, size = rs >> 4, rs & 15
                    if not size:
                        if run != 15:
                            break
                        k += 16
                        continue
                    k += run
                    bits.bits(size)
                    k += 1


def _mcu_colours(frame, dc, qt):
    out = []
    comps = frame['comps']
    for my in range(frame['my']):
        for mx in range(frame['mx']):
            means = []
            for comp in comps:
                values = [dc[comp['id']].get((mx * comp['h'] + h, my * comp['v'] + v))
                          for v in range(comp['v']) for h in range(comp['h'])]
                values = [x for x in values if x is not None]
                if not values:
                    break
                q = qt.get(comp['tq'], 1)
                means.append(sum(values) * q / (8.0 * len(values)) + 128.0)
            if len(means) != len(comps):
                continue
            if len(means) == 1:
                r = g = b = means[0]
            else:
                y, cb, cr = means[0], means[1], means[2]
                r = y + 1.402 * (cr - 128)
                g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
                b = y + 1.772 * (cb - 128)
            out.append(tuple(int(min(255, max(0, c))) for c in (r, g, b)))
    return out


def _pick(pixels):
    """The picture's most telling colour (frequent, saturated, neither too
    dark nor too bright) as (h, l, s), or None for a grey picture."""
    buckets = {}
    for r, g, b in pixels:
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if l < 0.08 or l > 0.92 or s < 0.18:
            continue
        key = (int(h * 24), int(s * 4), int(l * 4))
        entry = buckets.setdefault(key, [0, 0.0, 0.0, 0.0, 0.0])
        weight = s * (1.0 - abs(l - 0.5))
        entry[0] += 1
        entry[1] += r * weight
        entry[2] += g * weight
        entry[3] += b * weight
        entry[4] += weight
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda e: e[4] * (e[0] ** 0.5))
    if best[4] <= 0 or best[0] < max(2, len(pixels) // 60):
        return None
    r, g, b = best[1] / best[4], best[2] / best[4], best[3] / best[4]
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def _hex(h, l, s, alpha='FF'):
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return '%s%02X%02X%02X' % (alpha, int(r * 255), int(g * 255), int(b * 255))


def palette(pixels):
    """The page colours from a picture's block colours: its accent, a dark
    tone of it for the page and a glow."""
    found = _pick(pixels or [])
    if found is None:
        return None
    h, l, s = found
    return {'accent': _hex(h, min(0.66, max(0.5, l)), min(1.0, max(0.55, s))),
            'tone': _hex(h, 0.075, min(0.62, max(0.4, s))),
            'glow': _hex(h, 0.42, min(0.85, max(0.5, s)))}


def _small(url):
    if 'image.tmdb.org/t/p/' in url:
        head, _sep, tail = url.partition('/t/p/')
        return '%s/t/p/w300/%s' % (head, tail.split('/', 1)[-1])
    return url


def _fetch(url):
    if url.startswith(('http://', 'https://')):
        from urllib.request import Request, urlopen
        response = urlopen(Request(url, headers={'User-Agent': 'Kodi'}), timeout=6)
        try:
            return response.read(3 * 1024 * 1024)
        finally:
            response.close()
    import xbmcvfs
    handle = xbmcvfs.File(url)
    try:
        return bytes(handle.readBytes())
    finally:
        handle.close()


def _pil_pixels(data):
    """The picture's colours read by PIL (Kodi on the Mac ships it; the
    Apple TV build has none, and keeps to JPEGs)."""
    if not data:
        return None
    try:
        import io
        from PIL import Image
    except Exception:
        return None
    try:
        picture = Image.open(io.BytesIO(data)).convert('RGB')
        picture.thumbnail((48, 48))
        getdata = getattr(picture, 'get_flattened_data', None) or picture.getdata
        return list(getdata())
    except Exception:
        return None


def cached(url):
    with _LOCK:
        return _CACHE.get(str(url or '').strip(), False)


def colour(url):
    url = str(url or '').strip()
    if not url:
        return None
    with _LOCK:
        if url in _CACHE:
            return _CACHE[url]
    result = None
    try:
        data = _fetch(_small(url))
        pixels = block_means(data)
        if pixels is None:
            pixels = _pil_pixels(data)      # WebP, PNG: Kodi's image library, where it has one
        result = palette(pixels)
    except Exception:
        result = None
    with _LOCK:
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.clear()
        _CACHE[url] = result
    return result


# ------------------------------------------------------------ following
# v5.10.448: the title page and the collection page take the colour of the
# title on show (lighting choice "follows the title", Skin.String(dh.pagetone)
# = artwork): Window(Home).Property(dhs.art.tone) the page, dhs.art.glow its
# glow. A worker of the service works it out; the newest title wins.
PROP_TONE = 'dhs.art.tone'
PROP_GLOW = 'dhs.art.glow'
_WANT = {'url': None, 'shown': None}
_AHEAD = []                     # neighbours' pictures, worked out while idle
_WAKE = threading.Event()
_WORKER = {'thread': None}


def _publish(found):
    import xbmcgui
    home = xbmcgui.Window(10000)
    if found:
        home.setProperty(PROP_TONE, found['tone'])
        home.setProperty(PROP_GLOW, found['glow'])
    else:
        home.clearProperty(PROP_TONE)
        home.clearProperty(PROP_GLOW)


def _work():
    import xbmc
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if not _WAKE.wait(0.5):
            continue
        _WAKE.clear()
        url = _WANT['url']
        if not url or url == _WANT['shown']:
            continue
        found = None
        for one in url.split('\n'):
            found = colour(one)
            if found or _WANT['url'] != url:
                break
        if _WANT['url'] == url:          # still the title on show
            _WANT['shown'] = url
            _publish(found)


def _known(url):
    """The colour of the first candidate with one, when known without
    work; False while a candidate before it is still unknown."""
    for one in url.split('\n'):
        hit = cached(one)
        if hit is False:
            return False
        if hit:
            return hit
    return None


def known(*urls):
    return _known('\n'.join(u for u in (str(u or '').strip() for u in urls) if u)) is not False


def follow(*urls):
    """The page shows these pictures now, the first with a colour wins
    (the title's, then its collection's); none for no picture."""
    url = '\n'.join(u for u in (str(u or '').strip() for u in urls) if u)
    if url == _WANT['url']:
        return
    _WANT['url'] = url
    if not url:
        _WANT['shown'] = None
        _publish(None)
        return
    known = _known(url)
    if known is not False:                   # known already: at once
        _WANT['shown'] = url
        _publish(known)
        return
    thread = _WORKER['thread']
    if thread is None or not thread.is_alive():
        thread = threading.Thread(target=_work, name='DexHubArtColour')
        thread.daemon = True
        _WORKER['thread'] = thread
        thread.start()
    _WAKE.set()


def ahead(*urls):
    """Work out these pictures' colours while nothing else waits."""
    fresh = [u for u in (str(u or '').strip() for u in urls) if u and cached(u) is False]
    if not fresh:
        return
    _AHEAD[:] = fresh[:80]
    thread = _WORKER.get('ahead')
    if thread is None or not thread.is_alive():
        thread = threading.Thread(target=_work_ahead, name='DexHubArtAhead')
        thread.daemon = True
        _WORKER['ahead'] = thread
        thread.start()
    _AHEAD_WAKE.set()


_AHEAD_WAKE = threading.Event()


def _work_ahead():
    """The next titles' colours, one at a time, so moving to them is
    instant (cached)."""
    import xbmc
    monitor = xbmc.Monitor()
    while not monitor.abortRequested():
        if not _AHEAD_WAKE.wait(0.5):
            continue
        while _AHEAD and not monitor.abortRequested():
            try:
                one = _AHEAD.pop(0)
            except IndexError:
                break
            if cached(one) is False:
                colour(one)
        _AHEAD_WAKE.clear()


def stop_following():
    if _WANT['url'] or _WANT['shown']:
        _WANT['url'] = _WANT['shown'] = None
        _publish(None)
