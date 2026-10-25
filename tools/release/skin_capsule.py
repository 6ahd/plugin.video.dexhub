#!/usr/bin/env python3
"""Add the rating card to a skin (add-on 5.10.423, skin 3.28.138).

    python3 tools/release/skin_capsule.py SKIN_DIR [--check]

When a film starts, as a streaming service shows it: the age rating large,
what earned it (IMDb's Parents Guide), a quiet line with the video, the audio
and a rating, a short bar beside it and a soft shade behind; left, centred or
right. The add-on (resources/lib/player_capsule.py) publishes the text as
Home properties (dhs.cap.card.*, dhs.cap.place) and shows it with dhs.cap.on
(dhs.cap.osd with the player's menu).

What it writes:

* Includes_DexHub.xml: DhsCard
* VideoFullScreen.xml: the card when a video or another source starts, not
  over the player's menu
* VideoOSD.xml: the card while the menu is open, when the add-on asks
  (lower on the right, under the clock)
* media/dexhub: card_shade.png, card_shade_r.png, card_shade_c.png (the
  shade) and cap_white.png (the bar)

It also takes out what skins 3.28.134-3.28.137 had and the card replaced:
the playback pill (its images and the badges drawn for it) and Dex Hub's
design of TinyPPI's windows (TinyPPI is its own again).

The skin's copy is replaced each run (it sits between two comments), so
running it again brings a skin up to date. --check only reports.
"""
import argparse
import os
import re
import sys

BEGIN, END_MARK = '<!-- dhs.capsule (skin_capsule.py) -->', '<!-- /dhs.capsule -->'
TOP = 56
OSD_DROP = 86             # the clock sits top right in the player's menu
CARD_SIDE, CARD_W = 64, 900
# (property, font, colour, height): must match player_capsule.card's card.h
CARD_LINES = (('card.cert', 'dh_title', 'FFFFFFFF', 72), ('card.words', 'dh_label', 'EBFFFFFF', 40),
              ('card.info', 'font20_title', 'A6FFFFFF', 34))

# what 3.28.134-3.28.137 added for the pill and for TinyPPI's windows
OLD_MEDIA = ('cap_l.png', 'cap_px.png', 'cap_r.png', 'cap_mark.png', 'cap_fel.png', 'cap_mel.png',
             'ppi_glass.png', 'ppi_shadow.png', 'ppi_btn.png', 'ppi_bar.png')
OLD_BADGES = ('audio_dts_es', 'audio_dts_9624', 'audio_dts_express', 'audio_aac_he', 'audio_aac_hev2',
              'audio_aac_lc', 'audio_alac', 'audio_mp2', 'audio_mp1', 'ch_512', 'ch_712', 'other_imax')
OLD_TINYPPI = ('script-tinyppi-main.xml', 'script-tinyppi-dialog.xml', 'script-tinyppi-dialog-bar.xml',
               'script-tinyppi-dialog-single.xml')
OLD_TINYPPI_MARK = '<!-- dhs.tinyppi (skin_tinyppi.py) -->'


def P(name):
    return 'Window(Home).Property(dhs.cap.%s)' % name


def has(name):
    return '!String.IsEmpty(%s)' % P(name)


def eq(name, value):
    return 'String.IsEqual(%s,%s)' % (P(name), value)


def _image(width, height, texture, visible, top=0, left=0, diffuse=''):
    return ('<control type="image"><left>%d</left><top>%d</top><width>%d</width><height>%d</height>'
            '<visible>%s</visible><aspectratio>stretch</aspectratio><texture%s>%s</texture></control>'
            % (left, top, width, height, visible, (' colordiffuse="%s"' % diffuse) if diffuse else '', texture))


def card_include():
    """The age rating large, what earned it, the quiet line; a soft shade
    behind and a short bar beside, on the side the add-on picked."""
    heights = sorted({sum(h for (_p, _f, _c, h), on in zip(CARD_LINES, bits) if on)
                      for bits in ((a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1))} - {0})
    out = '<include name="DhsCard">'
    for place, align in (('top_left', 'left'), ('top_center', 'center'), ('top_right', 'right')):
        if place == 'top_left':
            visible = '!%s + !%s' % (eq('place', 'top_center'), eq('place', 'top_right'))
        else:
            visible = eq('place', place)
        text_left = {'left': CARD_SIDE + 24, 'right': 1920 - CARD_SIDE - 24 - CARD_W,
                     'center': (1920 - CARD_W) // 2}[align]
        shade = {'left': (0, 'card_shade.png', 1000), 'right': (1920 - 1000, 'card_shade_r.png', 1000),
                 'center': ((1920 - 1200) // 2, 'card_shade_c.png', 1200)}[align]
        slide = {'left': '-60,0', 'right': '60,0', 'center': '0,-30'}[align]
        body = _image(shade[2], 360, 'dexhub/' + shade[1], 'true', top=-TOP, left=shade[0])
        if align != 'center':
            x = CARD_SIDE if align == 'left' else 1920 - CARD_SIDE - 6
            body += ''.join(_image(6, h, 'dexhub/cap_white.png', eq('card.h', h), left=x, diffuse='FFC4B5FD')
                            for h in heights)
        rows = ''.join('<control type="label"><width>%d</width><height>%d</height><font>%s</font>'
                       '<textcolor>%s</textcolor><shadowcolor>99000000</shadowcolor><align>%s</align>'
                       '<aligny>center</aligny><visible>%s</visible><label>$INFO[%s]</label></control>'
                       % (CARD_W, h, font, colour, align, has(prop), P(prop)) for prop, font, colour, h in CARD_LINES)
        body += ('<control type="grouplist"><left>%d</left><top>0</top><width>%d</width><height>200</height>'
                 '<orientation>vertical</orientation><itemgap>0</itemgap>%s</control>' % (text_left, CARD_W, rows))
        out += ('<control type="group"><visible>%s</visible>'
                '<animation effect="fade" start="0" end="100" time="400">Visible</animation>'
                '<animation effect="slide" start="%s" end="0,0" time="500" tween="cubic" easing="out">Visible</animation>'
                '<animation effect="fade" start="100" end="0" time="500">Hidden</animation>%s</control>'
                % (visible, slide, body))
    return out + '</include>'


def window():
    return ('<control type="group"><top>%d</top>'
            '<visible>%s + !Window.IsVisible(videoosd) + !Window.IsVisible(seekbar) + '
            '!Window.IsVisible(script-tinyppi-main.xml)</visible><include>DhsCard</include></control>'
            % (TOP, has('on')))


def osd():
    return ('<control type="group"><top>%d</top><visible>%s + %s</visible>'
            '<animation effect="slide" start="0,0" end="0,%d" time="0" condition="%s">Conditional</animation>'
            '<include>DhsCard</include></control>' % (TOP, has('osd'), has('ready'), OSD_DROP, eq('place', 'top_right')))


def images(media):
    from PIL import Image
    w, h = 1000, 360
    img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    px = img.load()
    for y in range(h):
        fy = max(0.0, 1 - (y / float(h)) ** 2)
        for x in range(w):
            px[x, y] = (0, 0, 0, int(165 * max(0.0, 1 - x / float(w)) ** 1.6 * fy))
    img.save(os.path.join(media, 'card_shade.png'))
    img.transpose(Image.FLIP_LEFT_RIGHT).save(os.path.join(media, 'card_shade_r.png'))
    cw = 1200
    c = Image.new('RGBA', (cw, h), (0, 0, 0, 0))
    px = c.load()
    for y in range(h):
        for x in range(cw):
            d = ((x - cw / 2.0) / (cw / 2.0)) ** 2 + (y / float(h)) ** 2
            px[x, y] = (0, 0, 0, int(150 * max(0.0, 1 - d) ** 1.4))
    c.save(os.path.join(media, 'card_shade_c.png'))
    Image.new('RGBA', (8, 8), (255, 255, 255, 255)).save(os.path.join(media, 'cap_white.png'))
    return ['card_shade.png', 'card_shade_r.png', 'card_shade_c.png', 'cap_white.png']


def old_files(skin):
    """What the pill and the TinyPPI design left in the skin (TinyPPI's
    files only when the old tool wrote them)."""
    media = os.path.join(skin, 'media', 'dexhub')
    found = [os.path.join(media, n) for n in OLD_MEDIA]
    found += [os.path.join(media, 'badges', n + '.png') for n in OLD_BADGES]
    for folder in ('xml', os.path.join('extras', 'tinyppi')):
        for name in OLD_TINYPPI:
            path = os.path.join(skin, folder, name)
            if os.path.exists(path):
                with open(path, encoding='utf-8') as fh:
                    if OLD_TINYPPI_MARK in fh.read(512):
                        found.append(path)
    return [p for p in found if os.path.exists(p)]


def put(text, body, closing):
    """text with our block (replaced, or added before the last closing tag)."""
    block = BEGIN + '\n' + body + '\n' + END_MARK
    found = re.search(re.escape(BEGIN) + r'.*?' + re.escape(END_MARK), text, re.S)
    if found:
        return text[:found.start()] + block + text[found.end():]
    at = text.rfind(closing)
    if at < 0:
        raise ValueError('no %s' % closing)
    return text[:at] + block + '\n' + text[at:]


def run(skin, check=False):
    changed = []
    for path in old_files(skin):
        changed.append('removed ' + os.path.relpath(path, skin))
        if not check:
            os.remove(path)
    spare = os.path.join(skin, 'extras', 'tinyppi')
    if not check and os.path.isdir(spare) and not os.listdir(spare):
        os.rmdir(spare)
    xml = os.path.join(skin, 'xml')
    plan = ((os.path.join(xml, 'Includes_DexHub.xml'), card_include(), '</includes>'),
            (os.path.join(xml, 'VideoFullScreen.xml'), window(), '</controls>'),
            (os.path.join(xml, 'VideoOSD.xml'), osd(), '</controls>'))
    for path, body, closing in plan:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        new = put(text, body, closing)
        if new != text:
            changed.append(os.path.relpath(path, skin))
            if not check:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(new)
    if not check:
        changed += ['media/dexhub/' + n for n in images(os.path.join(skin, 'media', 'dexhub'))]
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
