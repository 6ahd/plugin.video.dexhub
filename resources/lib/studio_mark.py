"""A small, cached white wordmark for dark Home and title backgrounds.

No network access: remote logos have unknown colour/plates and are omitted.
Compiled resource textures can fall back to Kodi when its VFS cannot expose
individual image bytes. Pillow is optional; installed white packs remain usable.
"""
import hashlib
import io
import os
import struct

import xbmcvfs

_CACHE = {}
WIDTH, HEIGHT = 144, 40
# v5.10.448: the widths the Home hero keeps for a mark (skin: hero_studio_slot.py)
SLOTS = (48, 72, 96, 120, 144, 168, 192, 216, 240, 272)
_SLOT_CACHE = {}
_XBT_SIZES = {}


def render(image):
    """Trim transparent margins and fit the visible mark without stretching."""
    from PIL import Image, ImageChops
    if image.width > 4096 or image.height > 4096:
        return None
    image = image.convert('RGBA')
    alpha = image.getchannel('A')
    bbox = alpha.point(lambda a: 255 if a >= 8 else 0).getbbox()
    if bbox is None:
        return None
    # Some platform resources are square app tiles. Remove a uniform neutral
    # plate rather than painting the whole tile white. Other opaque artwork
    # is not a transparent studio mark and is deliberately omitted.
    if alpha.histogram()[255] > image.width * image.height * .7:
        w,h=image.size
        corners=[image.getpixel((x,y)) for x,y in [(w//10,h//10),(w-1-w//10,h//10),(w//10,h-1-h//10),(w-1-w//10,h-1-h//10)]]
        rgb=corners[0][:3]
        neutral=max(rgb)-min(rgb) <= 18
        uniform=all(max(abs(a-b) for a,b in zip(rgb,p[:3])) <= 18 for p in corners)
        if not (neutral and uniform):
            return None
        background=Image.new('RGB',image.size,rgb)
        diff=ImageChops.difference(image.convert('RGB'),background)
        channels=diff.split()
        mask=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2])
        alpha=ImageChops.multiply(alpha,mask)
        bbox=alpha.point(lambda a:255 if a>=8 else 0).getbbox()
        if bbox is None:
            return None
    alpha=alpha.crop(bbox)
    alpha.thumbnail((WIDTH,HEIGHT),Image.Resampling.LANCZOS)
    mark=Image.new('RGBA',(WIDTH,HEIGHT),(255,255,255,0))
    mark.paste((255,255,255,255),(0,(HEIGHT-alpha.height)//2,alpha.width,(HEIGHT-alpha.height)//2+alpha.height),alpha)
    return mark


def prepare(texture):
    """A verified transparent mark, or an empty value with no reserved space."""
    texture=str(texture or '').strip()
    if not texture:
        return ''
    if texture.startswith(('http://','https://')):
        # TMDb originals are commonly black. A URL is not proof of a white,
        # transparent studio mark; never flash it before a suitable pack mark.
        return ''
    bundled=texture.startswith(('resource://','xbt://'))
    if not bundled and not xbmcvfs.exists(texture):
        return ''
    if 'studio-marks-v1' in texture and xbmcvfs.exists(texture):
        return texture
    if texture in _CACHE:
        result=_CACHE[texture]
        if not result or result==texture or xbmcvfs.exists(result):
            return result
    try:
        from PIL import Image
    except ImportError:
        # Kodi normally has no Pillow. Only the known white pack is safe to
        # draw unprocessed; coloured pack/platform tiles can be dark or boxed.
        return texture if texture.startswith('resource://resource.images.studios.white/') else '' 
    try:
        handle=xbmcvfs.File(texture)
        try:data=bytes(handle.readBytes(4*1024*1024))
        finally:handle.close()
        mark=render(Image.open(io.BytesIO(data)))
        if mark is None:
            _CACHE[texture]=''
            return ''
        root=xbmcvfs.translatePath('special://profile/addon_data/plugin.video.dexhub/cache/studio-marks-v1')
        os.makedirs(root,exist_ok=True)
        filename=os.path.join(root,hashlib.sha256(texture.encode()).hexdigest()+'.png')
        tmp=filename+'.tmp'
        mark.save(tmp,format='PNG')
        os.replace(tmp,filename)
        _CACHE[texture]=filename
        return filename
    except Exception:
        # resource:// files in an XBT can be textures without readable bytes.
        result=texture if bundled else ''
        _CACHE[texture]=result
        return result


def slot(texture, chip=False):
    """v5.10.448: how wide the Home hero's line keeps the mark (one of SLOTS,
    as text), from the PNG's own size: the skin draws it 48 px high, so a
    short mark no longer leaves a wide gap before the server and ratings
    after it. '' when the size cannot be read (the skin keeps 272)."""
    texture = str(texture or '').strip()
    if not texture:
        return ''
    if '/platforms/' in texture:
        return '48'                       # a square app tile
    if chip:
        return '120'                      # the white plate is 104 wide
    if texture in _SLOT_CACHE:
        return _SLOT_CACHE[texture]
    result = ''
    try:
        size = _xbt_size(texture) if texture.startswith('resource://') else None
        if size is None:
            handle = xbmcvfs.File(texture)
            try:
                head = bytes(handle.readBytes(24))
            finally:
                handle.close()
            if head[:8] == b'\x89PNG\r\n\x1a\n' and head[12:16] == b'IHDR':
                size = struct.unpack('>II', head[16:24])
        if size and size[0] and size[1]:
            need = min(260.0, 48.0 * size[0] / size[1]) + 12
            result = str(next((n for n in SLOTS if n >= need), SLOTS[-1]))
    except Exception:
        result = ''
    _SLOT_CACHE[texture] = result
    return result


def _xbt_size(texture):
    """The (width, height) of a picture in an image pack, from its
    Textures.xbt index (the white studio pack is packed: its files cannot be
    read one by one). None when the pack is not packed or the name unknown."""
    rest = texture[len('resource://'):]
    addon, _, name = rest.partition('/')
    if addon not in _XBT_SIZES:
        sizes = {}
        for root in ('special://home/addons/', 'special://xbmc/addons/'):
            path = xbmcvfs.translatePath('%s%s/resources/Textures.xbt' % (root, addon))
            if not os.path.isfile(path):
                continue
            with open(path, 'rb') as handle:
                if handle.read(5) != b'XBTF2':
                    break
                for _ in range(struct.unpack('<I', handle.read(4))[0]):
                    key = handle.read(256).split(b'\0', 1)[0].decode('utf-8', 'replace').lower()
                    frames = struct.unpack('<II', handle.read(8))[1]
                    for frame in range(frames):
                        width, height = struct.unpack('<II', handle.read(8))
                        handle.read(32)               # format, sizes, duration, offset
                        if frame == 0:
                            sizes[key] = (width, height)
            break
        _XBT_SIZES[addon] = sizes
    return _XBT_SIZES[addon].get(name.lower())
