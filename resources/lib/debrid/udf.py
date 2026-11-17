# -*- coding: utf-8 -*-
"""The files of a Blu-ray disc image (.iso) read over the internet (v5.10.572).

A Blu-ray image holds the disc's UDF 2.50 file system. Its volume descriptors,
directories and file entries sit in the image's first megabytes, so a few
ranged reads of the link name every file of BDMV/ and CERTIFICATE/ with the
byte ranges of the image that hold it; discserver then serves the disc as a
folder, each file as those ranges of the link, and Kodi opens it in its menus.

The layout follows the image reader of the NuvioTV Usenet engine
(native/usenet/internal/engine/udf.go, GPL-3.0), written again here in Python:
the volume recognition sequence, the anchor at sector 256, the partition and
logical volume descriptors, a UDF 2.50 metadata partition through its metadata
file, (extended) file entries with short or long allocation descriptors and
their continuations, and the file identifier descriptors of each directory.
"""
import struct

SECTOR = 2048
CHUNK = 256 * 1024            # one ranged read of the link
CHUNKS = 48                   # kept in memory while the image is read
MAX_DIR = 16 << 20
MAX_FILES = 4096
MAX_DEPTH = 6
MAX_CONTINUING = 4096


class UDFError(Exception):
    pass


def _u16(b, at):
    return struct.unpack_from('<H', b, at)[0]


def _u32(b, at):
    return struct.unpack_from('<I', b, at)[0]


def _u64(b, at):
    return struct.unpack_from('<Q', b, at)[0]


def _tag(b, *ids):
    """The descriptor's identifier when its tag checksum holds and it is one of ids, else None."""
    if len(b) < 16:
        return None
    if sum(b[i] for i in range(16) if i != 4) & 0xFF != b[4]:
        return None
    ident = _u16(b, 0)
    return ident if ident in ids else None


def _long_ad(b, at=0):
    raw = _u32(b, at)
    return {'length': raw & 0x3FFFFFFF, 'kind': raw >> 30, 'block': _u32(b, at + 4), 'part': _u16(b, at + 8)}


def _name(b):
    if not b:
        return ''
    if b[0] in (8, 254):
        return bytes(b[1:]).decode('latin-1')
    if b[0] in (16, 255):
        return bytes(b[1:1 + (len(b) - 1) // 2 * 2]).decode('utf-16-be', 'replace')
    return ''


class Image(object):
    """A UDF image read through read(offset, length) -> bytes."""

    def __init__(self, read, size):
        self._read = read
        self.size = int(size)
        self._chunks = {}
        self._order = []
        self.parts = []           # [{'start': sector or -1, 'meta': [(offset, length)] or None}]
        self.root = None
        self.reads = 0

    # ---------------------------------------------------------------- bytes
    def read(self, off, n):
        if off < 0 or n < 0 or off + n > self.size:
            raise UDFError('a read outside the image')
        out = bytearray()
        while len(out) < n:
            at = (off + len(out)) // CHUNK * CHUNK
            chunk = self._chunk(at)
            start = off + len(out) - at
            out += chunk[start:start + n - len(out)]
            if start >= len(chunk):
                raise UDFError('the link gave fewer bytes than asked')
        return bytes(out)

    def _chunk(self, at):
        known = self._chunks.get(at)
        if known is not None:
            return known
        want = min(CHUNK, self.size - at)
        data = self._read(at, want)
        self.reads += 1
        if not data or len(data) < want:
            raise UDFError('the link gave %d of %d bytes at %d' % (len(data or b''), want, at))
        self._chunks[at] = data
        self._order.append(at)
        if len(self._order) > CHUNKS:
            self._chunks.pop(self._order.pop(0), None)
        return data

    # ---------------------------------------------------------------- volume
    def open(self):
        if self.size < 257 * SECTOR:
            raise UDFError('not a UDF image')
        nsr = False
        for sector in range(16, 32):
            ident = self.read(sector * SECTOR, 6)[1:6]
            if ident in (b'NSR02', b'NSR03'):
                nsr = True
                break
            if ident not in (b'BEA01', b'CD001', b'CDW02', b'BOOT2', b'TEA01'):
                break
        if not nsr:
            raise UDFError('not a UDF image')
        anchor = self.read(256 * SECTOR, SECTOR)
        if _tag(anchor, 2) is None:
            raise UDFError('no anchor')
        vds_length, vds_start = _u32(anchor, 16), _u32(anchor, 20)
        partitions, lvd = {}, None
        for sector in range(vds_start, vds_start + min(vds_length // SECTOR, 256)):
            b = self.read(sector * SECTOR, SECTOR)
            ident = _tag(b, 1, 3, 4, 5, 6, 7, 8)
            if ident is None or ident == 8:
                break
            if ident == 5:
                partitions[_u16(b, 22)] = _u32(b, 188)
            elif ident == 6 and lvd is None:
                lvd = b
        if lvd is None or _u32(lvd, 212) != SECTOR:
            raise UDFError('no logical volume')
        fsd = _long_ad(lvd, 248)
        maps = _u32(lvd, 268)
        table = lvd[440:440 + _u32(lvd, 264)]
        metas, p = [], 0
        for index in range(maps):
            if p + 2 > len(table) or table[p + 1] < 2 or p + table[p + 1] > len(table):
                raise UDFError('a partition map past its table')
            m = table[p:p + table[p + 1]]
            if m[0] == 1 and len(m) >= 6:
                if _u16(m, 4) not in partitions:
                    raise UDFError('a missing partition')
                self.parts.append({'start': partitions[_u16(m, 4)], 'meta': None})
            elif m[0] == 2 and len(m) >= 52 and m[5:28].startswith(b'*UDF Metadata Partition'):
                if _u16(m, 38) not in partitions:
                    raise UDFError('a missing partition')
                metas.append((index, _u16(m, 38), _u32(m, 40), _u32(m, 44)))
                self.parts.append({'start': -1, 'meta': None})
            else:
                raise UDFError('a partition kind of rewritable discs')
            p += table[p + 1]
        for index, physical, main, mirror in metas:
            start = partitions[physical]
            error = None
            for location in (main, mirror):
                try:
                    meta = self._entry(self.read((start + location) * SECTOR, SECTOR), 0, start)
                    self.parts[index]['meta'] = meta['extents']
                    error = None
                    break
                except UDFError as exc:
                    error = exc
            if error is not None:
                raise error
        b = self._block(fsd, SECTOR)
        if _tag(b, 256) is None:
            raise UDFError('no file set')
        self.root = _long_ad(b, 400)
        return self

    def _span(self, part, block, n):
        if part >= len(self.parts):
            raise UDFError('a missing partition')
        p = self.parts[part]
        off = block * SECTOR
        if p['meta'] is None:
            if p['start'] < 0:
                raise UDFError('a metadata partition without its file')
            if p['start'] * SECTOR + off + n > self.size:
                raise UDFError('past the image')
            return [(p['start'] * SECTOR + off, n)]
        out = []
        for m_off, m_len in p['meta']:
            if n == 0:
                break
            if off >= m_len:
                off -= m_len
                continue
            take = min(n, m_len - off)
            out.append((m_off + off, take))
            n -= take
            off = 0
        if n:
            raise UDFError('past the metadata file')
        return out

    def _block(self, ad, n):
        return b''.join(self.read(o, l) for o, l in self._span(ad['part'], ad['block'], n))

    def _spans_in(self, ad, physical):
        if physical > 0:
            off = (physical + ad['block']) * SECTOR
            if off + ad['length'] > self.size:
                raise UDFError('past the image')
            return [(off, ad['length'])]
        return self._span(ad['part'], ad['block'], ad['length'])

    def _block_in(self, ad, physical):
        if physical > 0:
            return self.read((physical + ad['block']) * SECTOR, SECTOR)
        return self._block({'part': ad['part'], 'block': ad['block']}, SECTOR)

    def _entry(self, b, part, physical):
        ident = _tag(b, 261, 266)
        if ident is None:
            raise UDFError('not a file entry')
        entry = {'dir': b[27] == 4, 'size': _u64(b, 56), 'extents': [], 'embedded': None}
        at = 216 if ident == 266 else 176
        ea, ad = _u32(b, at - 8), _u32(b, at - 4)
        if at + ea + ad > len(b):
            raise UDFError('descriptors past the entry')
        ads = b[at + ea:at + ea + ad]
        flags = b[34] & 7
        if flags == 3:
            if len(ads) < entry['size']:
                raise UDFError('a short embedded file')
            entry['embedded'] = bytes(ads[:entry['size']])
            return entry
        if flags not in (0, 1):
            raise UDFError('extended allocation descriptors')
        long_form = flags == 1
        size = 16 if long_form else 8
        total, hops = 0, 0
        while True:
            more = False
            while len(ads) >= size:
                if long_form:
                    a = _long_ad(ads)
                else:
                    raw = _u32(ads, 0)
                    a = {'length': raw & 0x3FFFFFFF, 'kind': raw >> 30, 'block': _u32(ads, 4), 'part': part}
                ads = ads[size:]
                if a['length'] == 0:
                    break
                if a['kind'] == 3:
                    hops += 1
                    if hops > MAX_CONTINUING:
                        raise UDFError('too many continuations')
                    aed = self._block_in(a, physical)
                    if _tag(aed, 258) is None:
                        raise UDFError('a bad continuation')
                    n = _u32(aed, 20)
                    ads, more = aed[24:24 + n], True
                    break
                if a['kind'] != 0:
                    raise UDFError('an unrecorded extent')
                entry['extents'].extend(self._spans_in(a, physical))
                total += a['length']
            if not more:
                break
        if total < entry['size']:
            raise UDFError('a file shorter than its size')
        excess = total - entry['size']
        while excess > 0 and entry['extents']:
            off, length = entry['extents'][-1]
            cut = min(excess, length)
            excess -= cut
            if length - cut:
                entry['extents'][-1] = (off, length - cut)
            else:
                entry['extents'].pop()
        return entry

    def file(self, icb):
        return self._entry(self._block(icb, SECTOR), icb['part'], 0)

    def data(self, entry, limit=MAX_DIR):
        if entry['embedded'] is not None:
            return entry['embedded']
        if entry['size'] > limit:
            raise UDFError('too large to read whole')
        return b''.join(self.read(o, l) for o, l in entry['extents'])

    def listing(self, directory):
        """[(name, is_dir, icb)] of a directory, without its parent and deleted entries."""
        b = self.data(directory)
        out = []
        while len(b) >= 38 and len(out) < 100000:
            if _tag(b, 257) is None:
                raise UDFError('a bad directory')
            chars, name_len, impl_len = b[18], b[19], _u16(b, 36)
            size = (38 + impl_len + name_len + 3) & ~3
            if 38 + impl_len + name_len > len(b):
                raise UDFError('a name past its directory')
            if chars & (4 | 8) == 0 and name_len > 0:
                out.append((_name(b[38 + impl_len:38 + impl_len + name_len]), bool(chars & 2), _long_ad(b, 20)))
            if size > len(b):
                break
            b = b[size:]
        return out

    # ---------------------------------------------------------------- the disc
    def disc_files(self):
        """{'BDMV/index.bdmv': {'size', 'x': [[offset, length], ...]}, ...} for every file of
        BDMV/ and CERTIFICATE/ (contiguous ranges joined); raises UDFError when there is no BDMV."""
        root = self.file(self.root)
        tops = {name.upper(): (is_dir, icb) for name, is_dir, icb in self.listing(root)}
        if 'BDMV' not in tops or not tops['BDMV'][0]:
            raise UDFError('no BDMV folder (a DVD image, or not a Blu-ray)')
        files = {}

        def walk(icb, prefix, depth):
            for name, is_dir, child in self.listing(self.file(icb)):
                if len(files) >= MAX_FILES:
                    return
                path = prefix + name
                if is_dir:
                    if depth < MAX_DEPTH:
                        walk(child, path + '/', depth + 1)
                    continue
                entry = self.file(child)
                if entry['embedded'] is not None:
                    files[path] = {'size': entry['size'], 'data': entry['embedded'].hex()}
                    continue
                joined = []
                for off, length in entry['extents']:
                    if joined and joined[-1][0] + joined[-1][1] == off:
                        joined[-1][1] += length
                    else:
                        joined.append([off, length])
                files[path] = {'size': entry['size'], 'x': joined}

        walk(tops['BDMV'][1], 'BDMV/', 0)
        if 'CERTIFICATE' in tops and tops['CERTIFICATE'][0]:
            walk(tops['CERTIFICATE'][1], 'CERTIFICATE/', 0)
        if not any(k.lower() == 'bdmv/index.bdmv' for k in files):
            raise UDFError('no BDMV/index.bdmv')
        return files
