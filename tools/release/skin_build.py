#!/usr/bin/env python3
"""Build a skin.dexhub / skin.dexhub.tvos release from its readable source.

    python3 tools/release/skin_build.py SRC_SKIN_DIR OUT_DIR [--version 3.28.124]
        [--xbt Textures.xbt | --texturepacker /path/to/TexturePacker] [--compact]

Every step is idempotent, so the source keeps the fixes once they are in it and
a release never depends on a hand-edited output:

* fixes (skin 3.28.123/124): the hero plot text box 68 high (60 clipped its
  second line); no videowindow inside list layouts (Kodi refuses them:
  "unsupported control type 35", one warning per card); "Ends at" only when
  the item has a duration (Kodi reports the current time for 0); the loading
  artwork window over the title page (zorder 3, 3.28.126)
* media/Textures.xbt: the images packed with ``TexturePacker -dupecheck``, as
  Kodi's own skins ship them (the PNGs stay for full-path lookups), or an
  .xbt built elsewhere with --xbt
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
VIDEO_RE = re.compile(r'\s*<control type="videowindow">(?:(?!<control).)*?</control>', re.S)
ZORDER_RE = re.compile(r'(<window\b[^>]*\bid="1161"[^>]*>(?:(?!</window>).)*?<zorder>)0(</zorder>)', re.S)
END_RE = re.compile(r'<value condition="([^"]*)">((?:(?!</value>).)*ListItem\.EndTime(?:(?!</value>).)*)</value>')


def fix_plot(text):
    return PLOT_RE.subn(r'\g<1>68\2', text)


def drop_layout_videowindows(text):
    out, last, gone = [], 0, 0
    for m in VIDEO_RE.finditer(text):
        before = text[:m.start()]
        opened = max(before.rfind('<focusedlayout'), before.rfind('<itemlayout'))
        closed = max(before.rfind('</focusedlayout>'), before.rfind('</itemlayout>'))
        if opened > closed:              # a card: Kodi never draws it there
            out.append(text[last:m.start()])
            last, gone = m.end(), gone + 1
    out.append(text[last:])
    return ''.join(out), gone


def raise_loading(text):
    """3.28.126: the loading artwork (Custom_1161) over the title page and
    the add-on's source windows. At zorder 0 it was drawn under the title
    page, which stays open until the video starts, so it was never seen."""
    return ZORDER_RE.subn(r'\g<1>3\2', text)


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

    totals = {'plot': 0, 'videowindow': 0, 'end_time': 0, 'loading': 0}
    for path in glob.glob(os.path.join(out, 'xml', '*.xml')):
        text = open(path, encoding='utf-8').read()
        new, n = guard_end_time(text)
        totals['end_time'] += n
        if os.path.basename(path) == 'Custom_1161_PlaybackLoading.xml':
            new, n = raise_loading(new)
            totals['loading'] += n
        if os.path.basename(path) == 'Home.xml':
            new, n = fix_plot(new)
            totals['plot'] += n
            new, n = drop_layout_videowindows(new)
            totals['videowindow'] += n
        if new != text:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(new)
    print('fixed: %(plot)d plot boxes, %(videowindow)d card videowindows, %(end_time)d end times, '
          '%(loading)d loading window order' % totals)

    xbt = os.path.join(out, 'media', 'Textures.xbt')
    if args.xbt:
        shutil.copyfile(args.xbt, xbt)
    elif args.texturepacker:
        if os.path.exists(xbt):
            os.remove(xbt)
        tmp = os.path.join(os.path.abspath(args.out), 'Textures.xbt')
        subprocess.run([args.texturepacker, '-dupecheck', '-input', os.path.join(out, 'media'), '-output', tmp],
                       check=True, stdout=subprocess.DEVNULL)
        shutil.move(tmp, xbt)
    if os.path.exists(xbt):
        print('media/Textures.xbt: %.1f MB' % (os.path.getsize(xbt) / 1048576.0))

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
