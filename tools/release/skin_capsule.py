#!/usr/bin/env python3
"""Add the playback capsule to a skin (add-on 5.10.417, skin 3.28.134).

    python3 tools/release/skin_capsule.py SKIN_DIR [--check]

A pill at the top of the picture with Dex Hub's mark and what is playing, two
lines per column: the video, the audio and the age rating. The add-on
(resources/lib/player_capsule.py) publishes the text and each column's width
class as Home properties (dhs.cap.*); the skin has a column for every class.

* Includes_DexHub.xml: DhsCapRow (the pill) and DhsCapCol (one column)
* VideoFullScreen.xml: the pill three times, one per place (dhs.cap.place)
* media/dexhub/cap_l.png, cap_r.png, cap_px.png: the pill's ends and middle;
  cap_mark.png: Dex Hub's mark in white

The skin's own copy is replaced each run (it sits between two comments), so
running it again only brings a skin up to date. --check only reports.
"""
import argparse
import os
import re
import sys

# must match player_capsule.STEP, MIN_W, MAX_W
STEP, MIN_W, MAX_W = 20, 100, 420
HEIGHT, PAD, TOP, SIDE = 92, 20, 44, 48
FILL = 'B8383842'
BEGIN, END = '<!-- dhs.capsule (skin_capsule.py) -->', '<!-- /dhs.capsule -->'
PLACES = (('top_left', 'left', '!String.IsEqual(Window(Home).Property(dhs.cap.place),top_center) + '
                                '!String.IsEqual(Window(Home).Property(dhs.cap.place),top_right)'),
          ('top_center', 'center', 'String.IsEqual(Window(Home).Property(dhs.cap.place),top_center)'),
          ('top_right', 'right', 'String.IsEqual(Window(Home).Property(dhs.cap.place),top_right)'))


def _prop(name):
    return 'Window(Home).Property(dhs.cap.%s)' % name


def includes():
    col = '''<include name="DhsCapCol">
	<control type="group">
		<width>$PARAM[ww]</width>
		<height>%(h)d</height>
		<visible>String.IsEqual(Window(Home).Property(dhs.cap.$PARAM[col].w),$PARAM[w])</visible>
		<control type="image">
			<width>$PARAM[ww]</width>
			<height>%(h)d</height>
			<texture colordiffuse="%(fill)s">dexhub/cap_px.png</texture>
		</control>
		<control type="label">
			<left>%(pad)d</left>
			<top>10</top>
			<width>$PARAM[w]</width>
			<height>40</height>
			<font>font30_title</font>
			<textcolor>FFFFFFFF</textcolor>
			<shadowcolor>66000000</shadowcolor>
			<aligny>center</aligny>
			<label>$INFO[Window(Home).Property(dhs.cap.$PARAM[col]1)]$INFO[Window(Home).Property(dhs.cap.$PARAM[col]1b),[COLOR A6FFFFFF] ,[/COLOR]]</label>
		</control>
		<control type="label">
			<left>%(pad)d</left>
			<top>50</top>
			<width>$PARAM[w]</width>
			<height>30</height>
			<font>dh_small</font>
			<textcolor>C8FFFFFF</textcolor>
			<shadowcolor>66000000</shadowcolor>
			<aligny>center</aligny>
			<label>$INFO[Window(Home).Property(dhs.cap.$PARAM[col]2)]</label>
		</control>
	</control>
</include>''' % {'h': HEIGHT, 'pad': PAD, 'fill': FILL}

    def divider(cond):
        return '''		<control type="group">
			<width>2</width>
			<height>%(h)d</height>
			<visible>%(cond)s</visible>
			<control type="image">
				<width>2</width>
				<height>%(h)d</height>
				<texture colordiffuse="%(fill)s">dexhub/cap_px.png</texture>
			</control>
			<control type="image">
				<top>22</top>
				<width>2</width>
				<height>%(line)d</height>
				<texture colordiffuse="40FFFFFF">dexhub/cap_px.png</texture>
			</control>
		</control>''' % {'h': HEIGHT, 'cond': cond, 'fill': FILL, 'line': HEIGHT - 44}

    def columns(name):
        return '\n'.join('\t\t<include content="DhsCapCol"><param name="col" value="%s"/>'
                         '<param name="w" value="%d"/><param name="ww" value="%d"/></include>'
                         % (name, w, w + 2 * PAD) for w in range(MIN_W, MAX_W + 1, STEP))

    has = lambda c: '!String.IsEmpty(%s)' % _prop(c + '.w')
    row = '''<include name="DhsCapRow">
	<control type="grouplist">
		<left>%(side)d</left>
		<top>%(top)d</top>
		<width>%(width)d</width>
		<height>%(h)d</height>
		<orientation>horizontal</orientation>
		<align>$PARAM[align]</align>
		<itemgap>0</itemgap>
		<usecontrolcoords>true</usecontrolcoords>
		<control type="group">
			<width>92</width>
			<height>%(h)d</height>
			<control type="image">
				<width>92</width>
				<height>%(h)d</height>
				<texture colordiffuse="%(fill)s">dexhub/cap_l.png</texture>
			</control>
			<control type="image">
				<left>24</left>
				<top>%(logo_top)d</top>
				<width>48</width>
				<height>48</height>
				<aspectratio>keep</aspectratio>
				<texture>dexhub/cap_mark.png</texture>
			</control>
		</control>
%(video)s
%(div_a)s
%(audio)s
%(div_r)s
%(rating)s
		<control type="image">
			<width>%(r)d</width>
			<height>%(h)d</height>
			<texture colordiffuse="%(fill)s">dexhub/cap_r.png</texture>
		</control>
	</control>
</include>''' % {'side': SIDE, 'top': TOP, 'width': 1920 - 2 * SIDE, 'h': HEIGHT, 'fill': FILL,
                 'logo_top': (HEIGHT - 48) // 2, 'r': HEIGHT // 2,
                 'video': columns('v'), 'audio': columns('a'), 'rating': columns('r'),
                 'div_a': divider('%s + %s' % (has('v'), has('a'))),
                 'div_r': divider('%s + [%s | %s]' % (has('r'), has('v'), has('a')))}
    return col + '\n' + row


def window():
    out = []
    for place, align, cond in PLACES:
        out.append('''<control type="group">
	<visible>!String.IsEmpty(Window(Home).Property(dhs.cap.on)) + %s + !Window.IsVisible(videoosd) + !Window.IsVisible(seekbar)</visible>
	<animation effect="fade" start="0" end="100" time="400">Visible</animation>
	<animation effect="slide" start="0,-30" end="0,0" time="450" tween="cubic" easing="out">Visible</animation>
	<animation effect="fade" start="100" end="0" time="600">Hidden</animation>
	<include content="DhsCapRow"><param name="align" value="%s"/></include>
</control>''' % (cond, align))
    return '\n'.join(out)


def _block(body):
    return BEGIN + '\n' + body + '\n' + END


def put(text, body, closing):
    """text with our block (replaced, or added before the last closing tag)."""
    block = _block(body)
    found = re.search(re.escape(BEGIN) + r'.*?' + re.escape(END), text, re.S)
    if found:
        return text[:found.start()] + block + text[found.end():]
    at = text.rfind(closing)
    if at < 0:
        raise ValueError('no %s' % closing)
    return text[:at] + block + '\n' + text[at:]


def images(media):
    from PIL import Image, ImageDraw
    k, h, r = 4, HEIGHT, HEIGHT // 2
    out = {}
    left = Image.new('L', (92 * k, h * k), 0)
    ImageDraw.Draw(left).rounded_rectangle((0, 0, 92 * k + r * k * 2, h * k - 1), radius=r * k, fill=255)
    right = Image.new('L', (r * k, h * k), 0)
    ImageDraw.Draw(right).rounded_rectangle((-r * k * 2, 0, r * k - 1, h * k - 1), radius=r * k, fill=255)
    for name, mask in (('cap_l.png', left), ('cap_r.png', right)):
        mask = mask.resize((mask.width // k, mask.height // k), Image.LANCZOS)
        img = Image.new('RGBA', mask.size, (255, 255, 255, 0))
        img.putalpha(mask)
        out[name] = img
    out['cap_px.png'] = Image.new('RGBA', (8, 8), (255, 255, 255, 255))
    mark = Image.open(os.path.join(media, 'brand_mark_lavender.png')).convert('RGBA')
    white = Image.new('RGBA', mark.size, (255, 255, 255, 0))
    white.putalpha(mark.split()[3])
    out['cap_mark.png'] = white                 # the mark in white, as on the pill
    for name, img in out.items():
        img.save(os.path.join(media, name))
    return sorted(out)


def run(skin, check=False):
    xml = os.path.join(skin, 'xml')
    plan = ((os.path.join(xml, 'Includes_DexHub.xml'), includes(), '</includes>'),
            (os.path.join(xml, 'VideoFullScreen.xml'), window(), '</controls>'))
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
    media = os.path.join(skin, 'media', 'dexhub')
    if check:
        return changed
    return changed + ['media/dexhub/' + n for n in images(media)]


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
