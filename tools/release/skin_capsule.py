#!/usr/bin/env python3
"""Add the playback capsule to a skin (add-on 5.10.418, skin 3.28.135).

    python3 tools/release/skin_capsule.py SKIN_DIR [--check]

A glass pill at the top of the picture with Dex Hub's mark and what is
playing: the video and the audio (badges, or words) on two lines each, and
the age rating with the logos and scores of the rating services. The add-on
(resources/lib/player_capsule.py) measures everything and publishes it as Home
properties (dhs.cap.*):

* each column's width class (dhs.cap.v.w, a.w, s.w): the skin has a
  background of every class (MIN_W to MAX_W in 20 px steps)
* the x of each line (bg, v1, v2, a1, a2, s) as bits: dhs.cap.<line>.<bit>
  shows a spacer of that many pixels in front of the line, so the line
  starts exactly where the add-on put it (Kodi cannot place a control from a
  property any other way)
* each badge and logo with its width (dhs.cap.vb1 and vb1.w, ...): the skin
  has an image of every width the badges and logos come in

What it writes:

* Includes_DexHub.xml: DhsCapPill (the pill) and DhsCapBits (the spacers)
* VideoFullScreen.xml: the pill when a video or another source starts
  (dhs.cap.on), not over the player's menu
* VideoOSD.xml: the pill while the player's menu is open (dhs.cap.osd), lower
  on the right, under the clock
* media/dexhub: cap_l.png, cap_px.png, cap_r.png (the glass: the ends and a
  slice of the middle, with its shadow), cap_mark.png (Dex Hub's mark in
  white), cap_fel.png and cap_mel.png (Dolby Vision's layer), and the badges
  the formats need that the skin did not have (DTS-ES, DTS 96/24, HE-AAC,
  5.1.2, IMAX, ...), drawn like the skin's own

The skin's copy is replaced each run (it sits between two comments), so
running it again brings a skin up to date. --check only reports.
"""
import argparse
import os
import re
import sys

# must match resources/lib/player_capsule.py
STEP, MIN_W, MAX_W = 20, 40, 600
LOGO, END, PAD, DIV = 92, 46, 20, 2
BADGE_H, ICON_H, GAP, SERVICE_GAP, VALUE_GAP = 40, 34, 8, 18, 8
BITS = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
HEIGHT, TOP, SHADOW_L, SHADOW_T, SHADOW_B = 92, 44, 16, 12, 24
TEX_H = SHADOW_T + HEIGHT + SHADOW_B
OSD_DROP = 86             # the clock sits top right in the player's menu
BEGIN, END_MARK = '<!-- dhs.capsule (skin_capsule.py) -->', '<!-- /dhs.capsule -->'

# badges a format needs that the skin may not have: file, words
NEW_BADGES = (('audio_dts_es', 'DTS-ES'), ('audio_dts_9624', 'DTS 96/24'), ('audio_dts_express', 'DTS EXPRESS'),
              ('audio_aac_he', 'HE-AAC'), ('audio_aac_hev2', 'HE-AAC V2'), ('audio_aac_lc', 'AAC-LC'),
              ('audio_alac', 'ALAC'), ('audio_mp2', 'MP2'), ('audio_mp1', 'MP1'),
              ('ch_512', '5.1.2'), ('ch_712', '7.1.2'), ('other_imax', 'IMAX'))
LAYERS = (('fel', 'FEL', (46, 160, 83)), ('mel', 'MEL', (255, 140, 26)))


def P(name):
    return 'Window(Home).Property(dhs.cap.%s)' % name


def has(name):
    return '!String.IsEmpty(%s)' % P(name)


def eq(name, value):
    return 'String.IsEqual(%s,%s)' % (P(name), value)


# ------------------------------------------------------------------ images
def _png_size(path):
    import struct
    with open(path, 'rb') as fh:
        head = fh.read(24)
    if head[:8] != b'\x89PNG\r\n\x1a\n':
        return None
    return struct.unpack('>II', head[16:24])


def drawn_width(path, height):
    """As the add-on works it out (player_capsule._png_width)."""
    size = _png_size(path)
    return int(round(size[0] * float(height) / size[1])) if size and size[1] else 0


def _font(skin, size):
    for name in ('AmazonEmberV2-Bold.ttf', 'InterArabic-Bold.ttf'):
        path = os.path.join(skin, 'fonts', name)
        if os.path.exists(path):
            from PIL import ImageFont
            return ImageFont.truetype(path, size)
    raise SystemExit('no bold font in %s/fonts' % skin)


def glass(media):
    """The glass, drawn once as a whole pill and cut: the left end (with its
    shadow), a one pixel slice of the middle and the right end."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    k, r = 4, HEIGHT // 2
    inner = 400
    w = SHADOW_L + LOGO + inner + END + SHADOW_L
    shadow = Image.new('RGBA', (w, TEX_H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((SHADOW_L, SHADOW_T + 8, w - SHADOW_L - 1, SHADOW_T + HEIGHT + 9),
                                             r, fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    pw = w - 2 * SHADOW_L
    mask = Image.new('L', (pw * k, HEIGHT * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw * k - 1, HEIGHT * k - 1), r * k, fill=255)
    mask = mask.resize((pw, HEIGHT), Image.LANCZOS)
    edge = Image.new('L', (pw * k, HEIGHT * k), 0)
    d = ImageDraw.Draw(edge)
    d.rounded_rectangle((0, 0, pw * k - 1, HEIGHT * k - 1), r * k, fill=255)
    d.rounded_rectangle((2 * k, 2 * k, pw * k - 1 - 2 * k, HEIGHT * k - 1 - 2 * k), r * k - 2 * k, fill=0)
    edge = edge.resize((pw, HEIGHT), Image.LANCZOS)
    body = Image.new('RGBA', (pw, HEIGHT))
    alpha = Image.new('L', (pw, HEIGHT))
    for y in range(HEIGHT):
        t = y / float(HEIGHT - 1)
        # darker to the bottom, a soft highlight over the upper half
        light = 16 * max(0.0, 1 - y / 46.0) if 3 <= y < 46 else 0
        base = (92 - 30 * t, 94 - 30 * t, 104 - 30 * t)
        body.paste(tuple(int(c + (255 - c) * light / 255.0) for c in base) + (255,), (0, y, pw, y + 1))
        alpha.paste(int(178 + 20 * t + light), (0, y, pw, y + 1))
    body.putalpha(ImageChops.multiply(alpha, mask))
    rim = Image.new('RGBA', (pw, HEIGHT), (255, 255, 255, 0))
    rim.putalpha(edge.point(lambda v: v * 46 // 255))
    body.alpha_composite(rim)
    whole = shadow.copy()
    whole.alpha_composite(body, (SHADOW_L, SHADOW_T))
    left = whole.crop((0, 0, SHADOW_L + LOGO, TEX_H))
    mid = whole.crop((SHADOW_L + LOGO + inner // 2, 0, SHADOW_L + LOGO + inner // 2 + 1, TEX_H))
    right = whole.crop((w - SHADOW_L - END, 0, w, TEX_H))
    out = {'cap_l.png': left, 'cap_px.png': mid, 'cap_r.png': right}
    for name, img in out.items():
        img.save(os.path.join(media, name))
    return sorted(out)


def marks(skin, media):
    from PIL import Image, ImageDraw
    out = []
    mark = Image.open(os.path.join(media, 'brand_mark_lavender.png')).convert('RGBA')
    white = Image.new('RGBA', mark.size, (255, 255, 255, 0))
    white.putalpha(mark.split()[3])
    white.save(os.path.join(media, 'cap_mark.png'))
    out.append('cap_mark.png')
    font = _font(skin, 60)
    for name, words, colour in LAYERS:
        tw = int(ImageDraw.Draw(Image.new('L', (1, 1))).textlength(words, font=font))
        img = Image.new('RGBA', (tw + 56, 112), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((3, 3, img.width - 4, 108), 26, fill=colour + (255,))
        d.text((img.width / 2.0, 57), words, font=font, fill=(255, 255, 255, 255), anchor='mm')
        img.save(os.path.join(media, 'cap_%s.png' % name))
        out.append('cap_%s.png' % name)
    return out


def badges(skin, media):
    """The formats' badges the skin lacks, drawn like its own: a white
    rounded frame and bold white words, 112 px high."""
    from PIL import Image, ImageDraw
    folder = os.path.join(media, 'badges')
    font = _font(skin, 58)
    made = []
    for name, words in NEW_BADGES:
        path = os.path.join(folder, name + '.png')
        if os.path.exists(path):        # the skin's own (or one drawn before) stays
            continue
        tw = int(ImageDraw.Draw(Image.new('L', (1, 1))).textlength(words, font=font))
        img = Image.new('RGBA', (tw + 68, 112), (255, 255, 255, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((3, 3, img.width - 4, 108), 24, outline=(255, 255, 255, 255), width=5)
        d.text((img.width / 2.0, 57), words, font=font, fill=(255, 255, 255, 255), anchor='mm')
        img.save(path)
        made.append('badges/' + name + '.png')
    return made


# ------------------------------------------------------------------ xml
def _widths(folder, height, names=None):
    out = set()
    for name in sorted(os.listdir(folder)):
        if name.endswith('.png') and (names is None or name[:-4] in names):
            w = drawn_width(os.path.join(folder, name), height)
            if w:
                out.add(w)
    return sorted(out)


def _image(width, height, texture, visible, top=0, left=0, diffuse=''):
    return ('<control type="image"><left>%d</left><top>%d</top><width>%d</width><height>%d</height>'
            '<visible>%s</visible><aspectratio>stretch</aspectratio><texture%s>%s</texture></control>'
            % (left, top, width, height, visible, (' colordiffuse="%s"' % diffuse) if diffuse else '', texture))


def _gap(width, visible):
    return ('<control type="image"><width>%d</width><height>4</height><visible>%s</visible></control>'
            % (width, visible))


def _label(font, colour, info, visible, height=40):
    return ('<control type="label"><width>auto</width><height>%d</height><font>%s</font><textcolor>%s</textcolor>'
            '<shadowcolor>66000000</shadowcolor><aligny>center</aligny><visible>%s</visible><label>%s</label></control>'
            % (height, font, colour, visible, info))


def _slot(prop, widths, height, texture, visible=''):
    """One badge or logo: an image of each width it may come in."""
    return ''.join(_image(w, height, texture, (visible + ' + ' if visible else '') + eq(prop + '.w', w))
                   for w in widths)


def _badge(prop, widths, visible=''):
    return _slot(prop, widths, BADGE_H, 'dexhub/badges/$INFO[%s].png' % P(prop), visible)


def _line(name, top, height, body, visible):
    return ('<control type="grouplist"><left>0</left><top>%d</top><width>1920</width><height>%d</height>'
            '<orientation>horizontal</orientation><itemgap>0</itemgap><usecontrolcoords>true</usecontrolcoords>'
            '<visible>%s</visible><include content="DhsCapBits"><param name="line" value="%s"/></include>%s</control>'
            % (top, height, visible, name, body))


def includes(skin):
    media = os.path.join(skin, 'media', 'dexhub')
    badge_w = _widths(os.path.join(media, 'badges'), BADGE_H)
    icon_w = _widths(os.path.join(media, 'ratings'), ICON_H)
    layer_w = {n: drawn_width(os.path.join(media, 'cap_%s.png' % n), BADGE_H) for n, _w, _c in LAYERS}
    classes = range(MIN_W, MAX_W + 1, STEP)
    bits = '<include name="DhsCapBits">%s</include>' % ''.join(
        _gap(b, '!String.IsEmpty(Window(Home).Property(dhs.cap.$PARAM[line].%d))' % b) for b in BITS)

    def background(col):
        return ''.join(_image(c + 2 * PAD, TEX_H, 'dexhub/cap_px.png', eq(col + '.w', c)) for c in classes)

    def divider(visible):
        return ('<control type="group"><width>%d</width><height>%d</height><visible>%s</visible>%s%s</control>'
                % (DIV, TEX_H, visible, _image(DIV, TEX_H, 'dexhub/cap_px.png', 'true'),
                   _image(DIV, HEIGHT - 44, 'dexhub/cap_px.png', 'true', top=SHADOW_T + 22, diffuse='40FFFFFF')))

    text, badged = eq('look', 'text'), '!' + eq('look', 'text')
    bg = (('<control type="group"><width>%d</width><height>%d</height>%s%s</control>'
           % (SHADOW_L + LOGO, TEX_H, _image(SHADOW_L + LOGO, TEX_H, 'dexhub/cap_l.png', 'true'),
              '<control type="image"><left>%d</left><top>%d</top><width>48</width><height>48</height>'
              '<aspectratio>keep</aspectratio><texture>dexhub/cap_mark.png</texture></control>'
              % (SHADOW_L + 22, SHADOW_T + (HEIGHT - 48) // 2)))
          + background('v') + divider('%s + %s' % (has('v.w'), has('a.w'))) + background('a')
          + divider('%s + [%s | %s]' % (has('s.w'), has('v.w'), has('a.w'))) + background('s')
          + _image(END + SHADOW_L, TEX_H, 'dexhub/cap_r.png', 'true'))
    tag = (_gap(GAP, has('vtag')) + ''.join(
        _image(layer_w[n], BADGE_H, 'dexhub/cap_%s.png' % n, eq('vtag', n)) for n, _w, _c in LAYERS if layer_w[n]))
    v1 = (_label('font30_title', 'FFFFFFFF', '$INFO[%s]$INFO[%s,[COLOR A6FFFFFF] ,[/COLOR]]' % (P('v1'), P('v1b')),
                 text)
          + _badge('vb1', badge_w, badged) + _gap(GAP, '%s + %s' % (badged, has('vb2')))
          + _badge('vb2', badge_w, badged) + _gap(GAP, '%s + %s' % (badged, has('vb3')))
          + _badge('vb3', badge_w, badged) + tag)
    a1 = (_label('font30_title', 'FFFFFFFF', '$INFO[%s]$INFO[%s,[COLOR A6FFFFFF] ,[/COLOR]]' % (P('a1'), P('a1b')),
                 text)
          + _badge('ab1', badge_w, badged) + _gap(GAP, '%s + %s' % (badged, has('ab2')))
          + _badge('ab2', badge_w, badged))
    services = _label('font30_title', 'FFFFFFFF', '$INFO[%s]' % P('s.cert'), has('s.cert'))
    for i in (1, 2, 3):
        lead = has('s.cert') if i == 1 else has('s%d.icon' % (i - 1))
        services += (_gap(SERVICE_GAP, '%s + %s' % (lead, has('s%d.icon' % i)))
                     + _slot('s%d' % i, icon_w, ICON_H, 'dexhub/ratings/$INFO[%s]' % P('s%d.icon' % i))
                     + _gap(VALUE_GAP, has('s%d.icon' % i))
                     + _label('font30_title', 'FFFFFFFF', '$INFO[%s]' % P('s%d.v' % i), has('s%d.icon' % i)))
    small = lambda name: _label('dh_small', 'D8FFFFFF', '$INFO[%s]' % P(name), has(name), height=30)
    top1, top2, mid = SHADOW_T + 10, SHADOW_T + 52, SHADOW_T + (HEIGHT - 40) // 2
    pill = ('<include name="DhsCapPill">'
            + _line('bg', 0, TEX_H, bg, 'true')
            + _line('v1', top1, BADGE_H, v1, has('v.w')) + _line('v2', top2, 30, small('v2'), has('v.w'))
            + _line('a1', top1, BADGE_H, a1, has('a.w')) + _line('a2', top2, 30, small('a2'), has('a.w'))
            + _line('s', mid, 40, services, has('s.w'))
            + '</include>')
    return bits + '\n' + pill


def window():
    return ('<control type="group"><top>%d</top>'
            '<visible>%s + !Window.IsVisible(videoosd) + !Window.IsVisible(seekbar) + '
            '!Window.IsVisible(script-tinyppi-main.xml)</visible>'
            '<animation effect="fade" start="0" end="100" time="400">Visible</animation>'
            '<animation effect="slide" start="0,-30" end="0,0" time="450" tween="cubic" easing="out">Visible</animation>'
            '<animation effect="fade" start="100" end="0" time="600">Hidden</animation>'
            '<include>DhsCapPill</include></control>' % (TOP - SHADOW_T, has('on')))


def osd():
    return ('<control type="group"><top>%d</top>'
            '<visible>%s + %s</visible>'
            '<animation effect="fade" start="0" end="100" time="300">Visible</animation>'
            '<animation effect="slide" start="0,0" end="0,%d" time="0" condition="%s">Conditional</animation>'
            '<include>DhsCapPill</include></control>'
            % (TOP - SHADOW_T, has('osd'), has('ready'), OSD_DROP, eq('place', 'top_right')))


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
    media = os.path.join(skin, 'media', 'dexhub')
    made = []
    if not check:      # the images first: the xml takes the badges' sizes from them
        made = (['media/dexhub/' + n for n in glass(media) + marks(skin, media)]
                + ['media/dexhub/' + n for n in badges(skin, media)])
    xml = os.path.join(skin, 'xml')
    plan = ((os.path.join(xml, 'Includes_DexHub.xml'), includes(skin), '</includes>'),
            (os.path.join(xml, 'VideoFullScreen.xml'), window(), '</controls>'),
            (os.path.join(xml, 'VideoOSD.xml'), osd(), '</controls>'))
    changed = []
    for path, body, closing in plan:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        new = put(text, body, closing)
        if new != text:
            changed.append(os.path.relpath(path, skin))
            if not check:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(new)
    return changed + made


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
