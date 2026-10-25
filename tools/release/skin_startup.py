#!/usr/bin/env python3
"""The Home comes whole at Kodi's start (add-on 5.10.424, skin 3.28.139).

    python3 tools/release/skin_startup.py SKIN_DIR [--check]

As Arctic Fuse 3 waits for its widgets on a splash: Dex Hub's screen covers
the Home while its first rows load, then goes, and the Home is there all at
once instead of row after row. The add-on's service watches the rows and
lifts it (resources/lib/skinui/startup_splash.py).

* xml/Custom_1198_DexStartup.xml: the cover (Dex Hub's wordmark, a moving
  bar), shown while the Home property dhs.splash is set; leaving takes the
  property with it
* xml/Startup.xml: raises it first thing, once per Kodi start (dhs.splash.seen),
  unless the setting is off (skin setting DexHub.NoStartupSplash), with a 20 s
  alarm that lifts it if the add-on's service never comes

The cover's file is the tool's own (rewritten each run); Startup.xml gets its
lines between two comments, so a second run only brings it up to date.
"""
import argparse
import os
import re
import sys

WINDOW = 'Custom_1198_DexStartup.xml'
BEGIN, END = '<!-- dhs.splash (skin_startup.py) -->', '<!-- /dhs.splash -->'
HOME = 'Window(Home).Property(%s)'
BACKGROUND = 'FF0D0E13'
ACCENT = 'FFC4B5FD'


def cover():
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!-- dhs.splash (skin_startup.py): the Home covered while its first rows load -->\n'
            '<window type="dialog" id="1198">\n'
            '\t<visible>!String.IsEmpty(%(prop)s) + !Skin.HasSetting(DexHub.NoStartupSplash)</visible>\n'
            '\t<onunload>ClearProperty(dhs.splash,home)</onunload>\n'
            '\t<animation effect="fade" start="100" end="0" time="450" tween="sine" easing="out">WindowClose</animation>\n'
            '\t<controls>\n'
            '\t\t<control type="image"><left>0</left><top>0</top><width>1920</width><height>1080</height>'
            '<texture colordiffuse="%(bg)s">dexhub/cap_white.png</texture></control>\n'
            '\t\t<control type="image"><left>679</left><top>482</top><width>562</width><height>96</height>'
            '<aspectratio>keep</aspectratio><texture>dexhub/brand_wordmark.png</texture>'
            '<animation effect="fade" start="70" end="100" time="1400" pulse="true" condition="true">Conditional</animation>'
            '</control>\n'
            '\t\t<control type="image"><left>800</left><top>630</top><width>320</width><height>4</height>'
            '<texture colordiffuse="26FFFFFF">dexhub/cap_white.png</texture></control>\n'
            '\t\t<control type="image"><left>800</left><top>630</top><width>80</width><height>4</height>'
            '<texture colordiffuse="%(accent)s">dexhub/cap_white.png</texture>'
            '<animation effect="slide" start="0,0" end="240,0" time="900" tween="sine" easing="inout" pulse="true" '
            'condition="true">Conditional</animation></control>\n'
            '\t</controls>\n'
            '</window>\n' % {'prop': HOME % 'dhs.splash', 'bg': BACKGROUND, 'accent': ACCENT})


def startup_lines():
    first = 'String.IsEmpty(%s) + !Skin.HasSetting(DexHub.NoStartupSplash)' % (HOME % 'dhs.splash.seen')
    return ('<onload condition="%s">SetProperty(dhs.splash,1,home)</onload>'
            '<onload condition="%s">AlarmClock(dhssplash,ClearProperty(dhs.splash,home),00:20,silent,false)</onload>'
            '<onload>SetProperty(dhs.splash.seen,1,home)</onload>' % (first, first))


def put_startup(text):
    block = BEGIN + startup_lines() + END
    found = re.search(re.escape(BEGIN) + r'.*?' + re.escape(END), text, re.S)
    if found:
        return text[:found.start()] + block + text[found.end():]
    m = re.search(r'<window[^>]*>', text)
    if not m:
        raise ValueError('Startup.xml has no <window>')
    return text[:m.end()] + block + text[m.end():]     # before it hands over to the Home


def run(skin, check=False):
    xml = os.path.join(skin, 'xml')
    changed = []
    for name, make in ((WINDOW, lambda old: cover()), ('Startup.xml', put_startup)):
        path = os.path.join(xml, name)
        old = ''
        if os.path.exists(path):
            with open(path, encoding='utf-8') as fh:
                old = fh.read()
        elif name == 'Startup.xml':
            raise SystemExit('%s has no xml/Startup.xml' % skin)
        new = make(old)
        if new != old:
            changed.append('xml/' + name)
            if not check:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(new)
    white = os.path.join(skin, 'media', 'dexhub', 'cap_white.png')
    if not os.path.exists(white):
        changed.append('media/dexhub/cap_white.png')
        if not check:
            from PIL import Image
            Image.new('RGBA', (8, 8), (255, 255, 255, 255)).save(white)
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('skin')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    changed = run(args.skin, args.check)
    print(('would change: ' if args.check else 'changed: ') + (', '.join(changed) or 'nothing'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
