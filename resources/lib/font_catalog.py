"""Discover local user media fonts and register a selected face in Kodi's skin.

Font.xml needs literal filenames. Only the chosen local media font is added,
atomically, with the same font roles as Default. No subtitle settings change.
"""
import copy
import hashlib
import os
import struct
from xml.etree import ElementTree as E

MEDIA_DIRS = ('special://home/media/Fonts/', 'special://home/media/fonts/',
              'special://home/media/', 'special://xbmc/media/Fonts/')
USER_PREFIX = 'User-'


def skin_font_file():
    import xbmcvfs
    return xbmcvfs.translatePath('special://home/addons/skin.dexhub/xml/Font.xml')


def font_name(path):
    """Read a bounded SFNT name table; filenames remain a safe fallback."""
    fallback = os.path.splitext(os.path.basename(path))[0].replace('_', ' ')
    try:
        with open(path, 'rb') as fh:
            header = fh.read(12)
            if len(header) != 12 or header[:4] not in (b'\0\1\0\0', b'true'):
                return ''
            count = struct.unpack_from('>H', header, 4)[0]
            if not 1 <= count <= 256:return ''
            records = fh.read(count * 16)
            if len(records) != count * 16:return ''
            total = os.fstat(fh.fileno()).st_size
            tags = set()
            offset = length = 0
            for i in range(count):
                tag, _, at, size = struct.unpack_from('>4sIII', records, i*16)
                if at + size > total:return ''
                tags.add(tag)
                if tag == b'name':offset,length=at,size
            if not {b'head',b'maxp',b'cmap',b'glyf'}.issubset(tags):return ''
            if not offset or not 6 <= length <= 1048576:return fallback
            fh.seek(offset);data=fh.read(length)
        _, count, strings = struct.unpack_from('>HHH',data)
        names=[]
        for i in range(min(count,1024)):
            platform,encoding,language,name_id,size,at=struct.unpack_from('>HHHHHH',data,6+i*12)
            if name_id not in (1,16):continue
            raw=data[strings+at:strings+at+size]
            value=raw.decode('utf-16-be' if platform in (0,3) else 'mac_roman',errors='replace').strip()
            if value and '\ufffd' not in value:
                names.append(((name_id==16,language in (0,0x409)),value))
        return max(names)[1] if names else fallback
    except (OSError,ValueError,struct.error,UnicodeError):return ''


def media_fonts(extra=''):
    import xbmcvfs
    found=[];seen=set()
    for directory in ((extra,) if extra else ()) + MEDIA_DIRS:
        try:
            real=xbmcvfs.translatePath(directory)
            if not os.path.isabs(real) or not os.path.isdir(real):continue
            for entry in sorted(os.scandir(real),key=lambda e:e.name.casefold()):
                if not entry.is_file() or not entry.name.lower().endswith('.ttf'):continue
                path=os.path.realpath(entry.path)
                if path in seen:continue
                seen.add(path);label=font_name(path)
                if not label:continue
                # Include the filename to distinguish regular/bold faces and
                # identical family names from different user directories.
                found.append({'id':USER_PREFIX+hashlib.sha256(path.encode()).hexdigest()[:16],
                              'label':label+' — '+entry.name,'source':path})
                if len(found)>=256:return found
        except OSError:continue
    return found


def presets():
    """Read the skin's own families instead of assuming a language or skin."""
    try:
        tree=E.parse(skin_font_file());out=[]
        for fs in tree.getroot().findall('fontset'):
            ident=fs.get('id') or ''
            if not ident or ' · ' in ident or ident.startswith(USER_PREFIX):continue
            out.append({'id':ident,'label':ident,'source':''})
        return out
    except (OSError,E.ParseError):return [{'id':'Default','label':'Default','source':''}]


def bold_style(font):
    style=font.find('style')
    if style is None:style=E.SubElement(font,'style')
    parts=(style.text or '').split()
    parts=[p for p in parts if p not in ('normal','lighten')]
    if 'bold' not in parts and 'bolditalics' not in parts:parts.append('bold')
    style.text=' '.join(parts)


def text_role(font):
    name=(font.findtext('name') or '').lower()
    file=(font.findtext('filename') or '').lower()
    return 'subtitle' not in name and not any(v in file for v in ('fa-solid','fa-regular','iconic-font'))


def register(family,source,scale,weight):
    """Install only the selected user font set; return whether XML changed."""
    if not family.startswith(USER_PREFIX):return False
    if not os.path.isabs(source) or not os.path.isfile(source) or not font_name(source):
        raise ValueError('The selected local font is unavailable')
    from .interface_fonts import fontset
    path=skin_font_file();tree=E.parse(path);root=tree.getroot()
    target=fontset(family,scale,weight)
    signature=hashlib.sha256((source+'|'+str(os.stat(source).st_mtime_ns)+'|'+str(os.stat(source).st_size)).encode()).hexdigest()
    for fs in root.findall('fontset'):
        if fs.get('id')==target and fs.get('dexhub_source')==signature:return False
    base=next(fs for fs in root.findall('fontset') if fs.get('id')=='Default')
    fs=copy.deepcopy(base);fs.set('id',target);fs.set('unicode','true');fs.set('dexhub_source',signature)
    fs.attrib.pop('idloc',None)   # its own name, not the Default set's (skin 3.28.161 names every set)
    for f in fs.findall('font'):
        if not text_role(f):continue
        f.find('filename').text=source
        size=f.find('size')
        if size is not None:size.text='%g' % (float(size.text)*int(scale)/100)
        if weight=='bold':bold_style(f)
    # Keep one selected custom face; a different profile regenerates its own
    # choice when activated. Old files themselves are never modified.
    for old in list(root.findall('fontset')):
        if (old.get('id') or '').startswith(USER_PREFIX):root.remove(old)
    root.append(fs)
    import tempfile
    fd,temp=tempfile.mkstemp(prefix='Font.user-',suffix='.xml',dir=os.path.dirname(path))
    try:
        with os.fdopen(fd,'wb') as fh:tree.write(fh,encoding='utf-8',xml_declaration=True)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return True
