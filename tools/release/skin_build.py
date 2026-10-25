#!/usr/bin/env python3
"""Build a skin.dexhub / skin.dexhub.tvos release from its readable source.

    python3 tools/release/skin_build.py SRC_SKIN_DIR OUT_DIR [--version 3.28.124]
        [--xbt Textures.xbt | --texturepacker /path/to/TexturePacker] [--compact]

Every step is idempotent, so the source keeps the fixes once they are in it and
a release never depends on a hand-edited output:

* fixes (skin 3.28.123/124): the hero plot text box 68 high (60 clipped its
  second line); "Ends at" only when
  the item has a duration (Kodi reports the current time for 0); the loading
  artwork window over the title page (zorder 3, 3.28.126); add-on and file
  icons in the Add-ons and File manager lists as rounded squares (3.28.127)
* media/Textures.xbt: the images packed with ``TexturePacker -dupecheck``, as
  Kodi's own skins ship them (the PNGs stay for full-path lookups), or an
  .xbt built elsewhere with --xbt; packed from plain 8-bit RGBA copies of the
  PNGs (TexturePacker swaps red and blue in palette PNGs and stripes 16-bit
  ones), and every entry is checked against its file (xbt_check.py) before
  it ships
* --compact: whitespace between tags removed in OUT only; each file is checked
  to hold the same elements, attributes and text as before, else kept as is

The source directory is never written to.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

PLOT_RE = re.compile(r'(<control type="textbox">(?:(?!</control>).)*?<height>)60'
                     r'(</height>(?:(?!</control>).)*?\$VAR\[DhsHeroPlot\])', re.S)
CIRCLE_RE = re.compile(r'(<control type="image">\s*)(<texture background="true" fallback="DefaultFolder.png") '
                       r'diffuse="af3/diffuse/circle/provider_circle.png">(\$INFO\[ListItem\.Icon\]</texture>\s*'
                       r'<aspectratio>keep</aspectratio>\s*)<bordersize>20</bordersize>')
# 3.28.129: source flags that named images the skin does not have (no
# af3/ in the path, or a name that is not there): those files got no flag
MISSING_FLAGS = (
    ('>flags/colorful/other/4K_web-dl.png<', '>af3/flags/colorful/other/web-dl.png<'),
    ('>flags/colorful/other/4K.png<', '>af3/flags/colorful/source/4k.png<'),
    ('>flags/colorful/other/hddvd.png<', '>af3/flags/colorful/source/hddvd.png<'),
    ('>flags/colorful/other/sdtv.png<', '>af3/flags/colorful/source/hdtv_old.png<'),
)
ZORDER_RE = re.compile(r'(<window\b[^>]*\bid="1161"[^>]*>(?:(?!</window>).)*?<zorder>)0(</zorder>)', re.S)
END_RE = re.compile(r'<value condition="([^"]*)">((?:(?!</value>).)*ListItem\.EndTime(?:(?!</value>).)*)</value>')


def fix_plot(text):
    return PLOT_RE.subn(r'\g<1>68\2', text)


def raise_loading(text):
    """3.28.126: the loading artwork (Custom_1161) over the title page and
    the add-on's source windows. At zorder 0 it was drawn under the title
    page, which stays open until the video starts, so it was never seen."""
    return ZORDER_RE.subn(r'\g<1>3\2', text)


def square_icons(text):
    """3.28.127: the Add-ons and File manager lists drew an add-on's icon
    through a 40 px circle stretched to 80 (blurred edges, corners of the
    square icons cut off) inside a 20 px bordersize. Now the rounded square
    of Settings' lists, 48 px in the middle of its 80 px place."""
    return CIRCLE_RE.subn(r'\1<left>16</left><top>16</top><width>48</width><height>48</height>'
                          r'\2 diffuse="af3/diffuse/square_w120_h120.png">\3', text)


def fix_flags(text):
    count = 0
    for old, new in MISSING_FLAGS:
        count += text.count(old)
        text = text.replace(old, new)
    return text, count


# 3.28.131: the network / service logo as 3.28.100 drew it. The unified mark
# (3.28.122) lost the title page's own logo (only the Home hero's was read,
# so a page opened from search or a related title showed none), the rounded
# square of the services' app icons (/platforms/) and the light chip behind
# dark logos.
TP_STUDIO = ('<variable name="DhsTpStudio"><value condition="$EXP[dhs_hr_item] + '
             '!String.IsEmpty(Window(Home).Property(dhs.hr.studio_logo))">$INFO[Window(Home).Property(dhs.hr.studio_logo)]</value>'
             '<value condition="String.IsEqual(Window(Home).Property(dhs.tp.id),ListItem.Property(dhs.id)) + '
             '!String.IsEmpty(Window(Home).Property(dhs.tp.studio_logo))">$INFO[Window(Home).Property(dhs.tp.studio_logo)]</value>'
             '<value>$INFO[ListItem.Property(dhs.studio_logo)]</value></variable>')
TP_HAS_STUDIO = ('<expression name="dhs_tp_has_studio">[[$EXP[dhs_hr_item] + !String.IsEmpty(Window(Home).Property(dhs.hr.studio_logo))] | '
                 '[String.IsEqual(Window(Home).Property(dhs.tp.id),ListItem.Property(dhs.id)) + '
                 '!String.IsEmpty(Window(Home).Property(dhs.tp.studio_logo))] | '
                 '[!String.IsEqual(Window(Home).Property(dhs.tp.id),ListItem.Property(dhs.id)) + '
                 '!String.IsEmpty(ListItem.Property(dhs.studio_logo))]] + '
                 '!String.IsEqual(Window(Home).Property(dhs.pref.metadata_badges),false)</expression>')
MARK = ('<include name="DhStudioMark"><param name="texture">$VAR[DhsHeroStudio]</param>'
        '<param name="square">dhs_hero_studio_square</param><param name="chip">dhs_hero_studio_chip</param><definition>'
        # a service's app icon: a rounded square
        '<control type="image"><left>0</left><top>2</top><width>36</width><height>36</height><aspectratio>scale</aspectratio>'
        '<texture background="true" diffuse="dexhub/mask_square.png">$PARAM[texture]</texture>'
        '<visible>$EXP[$PARAM[square]]</visible></control>'
        # a dark logo: on a light chip
        '<control type="group"><visible>![$EXP[$PARAM[square]]] + $EXP[$PARAM[chip]]</visible>'
        '<control type="image"><left>0</left><top>2</top><width>104</width><height>36</height>'
        '<texture colordiffuse="E6FFFFFF" border="12">dexhub/tile_bg.png</texture></control>'
        '<control type="image"><left>10</left><top>7</top><width>84</width><height>26</height><aspectratio>keep</aspectratio>'
        '<texture background="true">$PARAM[texture]</texture></control></control>'
        # any other logo: as it is, with its soft shadow
        '<control type="group"><visible>![$EXP[$PARAM[square]]] + ![$EXP[$PARAM[chip]]]</visible>'
        '<control type="image"><left>1</left><top>1</top><width>144</width><height>40</height>'
        '<aspectratio align="left" aligny="center">keep</aspectratio><texture background="true" colordiffuse="70000000">$PARAM[texture]</texture>'
        '<fadetime>0</fadetime></control><control type="image"><left>0</left><top>0</top><width>144</width><height>40</height>'
        '<aspectratio align="left" aligny="center">keep</aspectratio><texture background="true">$PARAM[texture]</texture>'
        '<fadetime>0</fadetime></control></control></definition></include>')
TP_CALL = '<include>DhStudioMark<param name="texture" value="$VAR[DhsTpStudio]" /></include>'
TP_CALL_NEW = ('<include>DhStudioMark<param name="texture" value="$VAR[DhsTpStudio]" />'
               '<param name="square" value="dhs_tp_studio_square" /><param name="chip" value="dhs_tp_studio_chip" /></include>')


def studio_marks(name, text):
    count = 0
    if name == 'Includes_DexHub.xml':
        for pattern, new in ((r'<variable name="DhsTpStudio">.*?</variable>', TP_STUDIO),
                             (r'<expression name="dhs_tp_has_studio">[^<]*</expression>', TP_HAS_STUDIO),
                             (r'<include name="DhStudioMark">.*?</include>', MARK)):
            text, n = re.subn(pattern, lambda m: new, text, count=1, flags=re.S)
            count += n
    elif name == 'DialogVideoInfo.xml' and TP_CALL in text:
        text, count = text.replace(TP_CALL, TP_CALL_NEW), text.count(TP_CALL)
    return text, count


# 3.28.132: OK on a season goes to its episodes. With more than 3 seasons it
# opened the list of all seasons, even when the cursor was already on the
# one wanted, and picking from it moved the focus a second time. The list
# stays on a long press.
SEASON_OK = ('<onclick condition="Integer.IsGreater(Container(8200).NumItems,3)">'
             'RunPlugin(plugin://{base}/?action=title_seasons_pick)</onclick>'
             '<onclick condition="!Integer.IsGreater(Container(8200).NumItems,3)">Control.SetFocus(8300)</onclick>')
SEASON_OK_NEW = ('<onclick>Control.SetFocus(8300)</onclick>'
                 '<onlongclick condition="Integer.IsGreater(Container(8200).NumItems,3)">'
                 'RunPlugin(plugin://{base}/?action=title_seasons_pick)</onlongclick>')


def season_ok(name, text):
    if name != 'DialogVideoInfo.xml':
        return text, 0
    count = 0
    for base in ('plugin.video.dexhub', 'plugin.video.dexhub.tvos'):
        old = SEASON_OK.format(base=base)
        if old in text:
            count += text.count(old)
            text = text.replace(old, SEASON_OK_NEW.format(base=base))
    return text, count


def guard_end_time(text):
    count = [0]

    def fix(m):
        cond, body = m.group(1), m.group(2)
        c = re.search(r'(Container\(\d+\)\.)?ListItem\.EndTime(?:Resume)?', body)
        if not c or 'Duration,0' in cond:
            return m.group(0)
        count[0] += 1
        return '<value condition="%s + Integer.IsGreater(%sListItem.Duration,0)">%s</value>' % (
            cond, c.group(1) or '', body)
    return END_RE.sub(fix, text), count[0]


def _shape(path):
    def walk(node):
        yield node.tag, tuple(sorted(node.attrib.items())), (node.text or '').strip(), (node.tail or '').strip()
        for child in node:
            yield from walk(child)
    return list(walk(ET.parse(path).getroot()))


def compact(path):
    """Whitespace between tags removed when the file keeps the same content."""
    text = open(path, encoding='utf-8').read()
    small = re.sub(r'>\s+<', '><', text)
    if small == text:
        return False
    tmp = path + '.compact'
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(small)
    try:
        same = _shape(path) == _shape(tmp)
    except ET.ParseError:
        same = False
    if same:
        os.replace(tmp, path)
    else:
        os.remove(tmp)
    return same


def plain_pngs(media):
    """Every PNG under media that is not plain 8-bit RGBA, saved as that
    (3.28.140). Kodi's TexturePacker sets BGR order only for RGB and RGBA
    PNGs, so a palette PNG went into the pack as RGBA marked BGRA (red and
    blue swapped on screen), and it reads 16-bit channels as they are (the
    65 striped icons of 3.28.124). Only the copy that is packed is changed;
    the skin's own files stay as they are."""
    from PIL import Image
    count = 0
    for root, _dirs, files in os.walk(media):
        for name in files:
            if not name.lower().endswith('.png'):
                continue
            path = os.path.join(root, name)
            with open(path, 'rb') as fh:
                head = fh.read(4096)
            plain = (head[:8] == b'\x89PNG\r\n\x1a\n' and head[24] == 8 and head[25] == 6
                     and b'tRNS' not in head)
            if not plain:
                image = Image.open(path).convert('RGBA')
                image.info.pop('icc_profile', None)     # a grey profile does not fit RGBA
                image.save(path)
                count += 1
    return count


def set_version(skin_dir, version):
    path = os.path.join(skin_dir, 'addon.xml')
    text = open(path, encoding='utf-8').read()
    text = re.sub(r'(<addon\b[^>]*\bversion=")[^"]+(")', r'\g<1>%s\2' % version, text, count=1)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--version')
    ap.add_argument('--xbt', help='a Textures.xbt built elsewhere')
    ap.add_argument('--texturepacker', help='TexturePacker to build media/Textures.xbt with')
    ap.add_argument('--compact', action='store_true')
    args = ap.parse_args(argv)

    src = os.path.abspath(args.src.rstrip('/'))
    out = os.path.join(os.path.abspath(args.out), os.path.basename(src))
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(src, out, ignore=shutil.ignore_patterns('__pycache__', '*.compact'))

    totals = {'plot': 0, 'end_time': 0, 'loading': 0, 'icons': 0, 'flags': 0, 'studio': 0, 'season': 0}
    for path in glob.glob(os.path.join(out, 'xml', '*.xml')):
        text = open(path, encoding='utf-8').read()
        new, n = guard_end_time(text)
        totals['end_time'] += n
        new, n = square_icons(new)
        totals['icons'] += n
        new, n = fix_flags(new)
        totals['flags'] += n
        new, n = studio_marks(os.path.basename(path), new)
        totals['studio'] += n
        new, n = season_ok(os.path.basename(path), new)
        totals['season'] += n
        if os.path.basename(path) == 'Custom_1161_PlaybackLoading.xml':
            new, n = raise_loading(new)
            totals['loading'] += n
        if os.path.basename(path) == 'Home.xml':
            new, n = fix_plot(new)
            totals['plot'] += n
        if new != text:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(new)
    # 3.28.128: the cards' videowindows stay; they are the trailer inside the
    # focused card (3.28.124 took them out and the card showed black)
    print('fixed: %(plot)d plot boxes, %(end_time)d end times, '
          '%(loading)d loading window order, %(icons)d list icons, %(flags)d source flags, '
          '%(studio)d studio mark parts, %(season)d season OK' % totals)

    xbt = os.path.join(out, 'media', 'Textures.xbt')
    if args.xbt:
        shutil.copyfile(args.xbt, xbt)
    elif args.texturepacker:
        if os.path.exists(xbt):
            os.remove(xbt)
        tmp = os.path.join(os.path.abspath(args.out), 'Textures.xbt')
        # 3.28.140: packed from plain copies (TexturePacker drew palette PNGs
        # with red and blue swapped and 16-bit ones as stripes)
        plain = os.path.join(os.path.abspath(args.out), 'media-plain')
        shutil.rmtree(plain, ignore_errors=True)
        shutil.copytree(os.path.join(out, 'media'), plain)
        print('media for the pack: %d image(s) made plain 8-bit RGBA' % plain_pngs(plain))
        subprocess.run([args.texturepacker, '-dupecheck', '-input', plain, '-output', tmp],
                       check=True, stdout=subprocess.DEVNULL)
        shutil.rmtree(plain, ignore_errors=True)
        shutil.move(tmp, xbt)
    if os.path.exists(xbt):
        # 3.28.133: a pack whose images differ from the files is left out (Kodi
        # reads the pack first: the 3.28.124 one drew 65 icons as stripes)
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import xbt_check
        bad = xbt_check.damaged(out)
        if bad:
            os.remove(xbt)
            print('media/Textures.xbt left out: %d damaged image(s), e.g. %s' % (
                len(bad), ', '.join(name for name, _why in bad[:3])))
        else:
            print('media/Textures.xbt: %.1f MB, checked' % (os.path.getsize(xbt) / 1048576.0))

    if args.compact:
        kept = [os.path.basename(p) for p in glob.glob(os.path.join(out, 'xml', '*.xml')) if not compact(p)]
        print('compacted; left as they were: %s' % (', '.join(sorted(kept)) or 'none'))
    if args.version:
        set_version(out, args.version)
    for path in glob.glob(os.path.join(out, 'xml', '*.xml')) + [os.path.join(out, 'addon.xml')]:
        ET.parse(path)                  # every file still parses
    print('ready: %s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
