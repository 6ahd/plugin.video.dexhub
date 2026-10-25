#!/usr/bin/env python3
"""Add the title page's Reviews and Pictures rows and the island's Friends tab
to a skin (add-on 5.10.406).

    python3 tools/release/skin_title_social.py SKIN_DIR [--check]

Both skins (skin.dexhub, skin.dexhub.tvos) get the same rows; the add-on id in
the content paths is read from the skin's own Cast row. Idempotent: a skin
that has them already is left alone.

* Includes_DexHub.xml: DhsTpReviewsPath / DhsTpArtworkPath, made from
  DhsTpCastPath (same owner check, the rows load once the page is the item's)
* DialogVideoInfo.xml: the two sections after Cast (lists 8450 and 8460)
* every up/down chain on the page learns the two lists: a chain is rebuilt
  from its targets in page order and must first rebuild to exactly what the
  skin has, else the file is left as it was and the chain is reported

* Custom_1195_DexPanel.xml: a Friends tab (what the people you follow on
  Trakt watched) after Monitoring; the tabs are a little narrower to fit it

--check only reports what would change.
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

REVIEWS, ARTWORK = 8450, 8460
ORDER = [8200, 8300, 8400, REVIEWS, ARTWORK, 8500, 8600, 8700]   # the page, top to bottom
SERIES = (8200, 8300)


def pos(cid):
    if cid in SERIES:
        return '[$EXP[dhs_series] + Integer.IsGreater(Container(%d).NumItems,0)]' % cid
    return '[Integer.IsGreater(Container(%d).NumItems,0)]' % cid


def _neg(cids):
    return '![%s]' % ' | '.join(pos(c) for c in cids)


# ------------------------------------------------------------------ chains
CHAIN_TAG = re.compile(r'<control\b[^>]*>|</control>|<(ondown|onup) condition="([^"]*)">([^<]*)</\1>')


def chains(text):
    """[(start, end, direction, source id or None, [(cond, target)])]: runs of
    conditional ondown/onup next to each other that reach a list of the page."""
    out, stack, run = [], [], None

    def close():
        if run and any(t.isdigit() and int(t) in ORDER for _c, t in run[4]):
            out.append(run)
    for m in CHAIN_TAG.finditer(text):
        tag = m.group(0)
        if m.group(1):
            if run and run[2] == m.group(1) and run[1] == m.start():
                run[1] = m.end()
                run[4].append((m.group(2), m.group(3)))
                continue
            close()
            src = next((s for s in reversed(stack) if s is not None), None)
            run = [m.start(), m.end(), m.group(1), src, [(m.group(2), m.group(3))]]
            continue
        if tag.startswith('</'):
            if stack:
                stack.pop()
        else:
            ident = re.search(r'\bid="(\d+)"', tag)
            stack.append(int(ident.group(1)) if ident else None)
    close()
    return [tuple(r) for r in out]


def _rank(cid, direction):
    """Where a control sits on the page; the buttons (and anything else) are above."""
    if cid in ORDER:
        return ORDER.index(cid)
    return -1 if direction == 'ondown' else len(ORDER)


def classify(entries):
    """[(kind, cond, target)]: 'list' entries are their own test plus 'none of
    the earlier ones', the last one 'none of them' ('rest': noop / the
    buttons); any other shape ('keep': the buttons' 'back to the season you
    were on') stays as it is."""
    out, seen = [], []
    for index, (cond, target) in enumerate(entries):
        cid = int(target) if target.isdigit() and int(target) in ORDER else None
        if cid and cond == pos(cid) + (' + ' + _neg(seen) if seen else ''):
            out.append(('list', cond, target))
        elif index == len(entries) - 1 and seen and cond == _neg(seen):
            out.append(('rest', cond, target))
        else:
            out.append(('keep', cond, target))
        if cid and cid not in seen:
            seen.append(cid)
    return out


def generate(classified):
    out, seen = [], []
    for kind, cond, target in classified:
        cid = int(target) if target.isdigit() and int(target) in ORDER else None
        if kind == 'list':
            cond = pos(cid) + (' + ' + _neg(seen) if seen else '')
        elif kind == 'rest':
            cond = _neg(seen)
        out.append((cond, target))
        if cid and cid not in seen:
            seen.append(cid)
    return out


def grow(classified, direction, source):
    """The chain with Reviews and Pictures where they sit on the page."""
    have = [int(t) for _k, _c, t in classified if t.isdigit() and int(t) in ORDER]
    if REVIEWS in have or ARTWORK in have:
        return None
    down = direction == 'ondown'
    src = _rank(source, direction)
    new = [c for c in (REVIEWS, ARTWORK) if (ORDER.index(c) > src if down else ORDER.index(c) < src)]
    if not new:
        return None
    out = list(classified)
    for cid in (new if down else list(reversed(new))):
        at = len(out) - 1 if out and out[-1][0] == 'rest' else len(out)
        for i, (kind, _c, t) in enumerate(out):
            if t.isdigit() and int(t) in ORDER and (ORDER.index(int(t)) > ORDER.index(cid) if down
                                                    else ORDER.index(int(t)) < ORDER.index(cid)):
                at = i
                break
        out.insert(at, ('list', '', str(cid)))
    return out


def _write(entries, direction):
    return ''.join('<%s condition="%s">%s</%s>' % (direction, c, t, direction) for c, t in entries)


def patch_chains(text, name, problems):
    out, last, changed = [], 0, 0
    for start, end, direction, source, entries in chains(text):
        classified = classify(entries)
        if _write(generate(classified), direction) != text[start:end]:
            problems.append('%s: chain of %s does not rebuild, left as is' % (name, source))
            continue
        grown = grow(classified, direction, source)
        if not grown:
            continue
        out.append(text[last:start])
        out.append(_write(generate(grown), direction))
        last, changed = end, changed + 1
    out.append(text[last:])
    return ''.join(out), changed


def chain_for(source, direction, fallback):
    down = direction == 'ondown'
    targets = [c for c in ORDER if (ORDER.index(c) > ORDER.index(source) if down
                                    else ORDER.index(c) < ORDER.index(source))]
    targets = targets if down else list(reversed(targets))
    return _write(generate([('list', '', str(c)) for c in targets] + [('rest', '', fallback)]), direction)


# ------------------------------------------------------------------ the rows
GLOW = ('<control type="image"><left>{gl}</left><top>{gt}</top><width>{gw}</width><height>{gh}</height>'
        '<texture border="60">dexhub/glow.png</texture><colordiffuse>$VAR[DhGlowFocus]</colordiffuse>'
        '<visible>[Control.HasFocus({cid})] + !String.IsEqual(Skin.String(dh.focusglow),off)</visible></control>')
ZOOM = ('<animation effect="zoom" start="100" end="103" time="160" tween="cubic" easing="out" center="auto" '
        'condition="Control.HasFocus({cid})">Conditional</animation>')
CARD = ('<control type="image"><left>0</left><top>0</top><width>{w}</width><height>{h}</height>'
        '<texture border="16">dexhub/tile_bg.png</texture><colordiffuse>$VAR[DhCard]</colordiffuse></control>')
ONCLICK = ('<onleft>noop</onleft><onright>noop</onright>'
           '<onclick condition="String.IsEqual(ListItem.Property(dhs.click),run)">'
           'RunPlugin($ESCINFO[ListItem.Property(dhs.target)])</onclick>')
KIND = 'String.IsEqual(ListItem.Property(dhs.kind),%s)'


def _label(left, top, width, height, font, color, label, align='left', visible=None, extra=''):
    return ('<control type="label"><left>%d</left><top>%d</top><width>%d</width><height>%d</height><font>%s</font>'
            '<textcolor>%s</textcolor><align>%s</align><aligny>center</aligny>%s<label>%s</label>%s</control>' % (
                left, top, width, height, font, color, align, extra, label,
                '<visible>%s</visible>' % visible if visible else ''))


def _image(left, top, width, height, texture, color=None, visible=None, extra=''):
    return ('<control type="image"><left>%d</left><top>%d</top><width>%d</width><height>%d</height>%s'
            '<texture%s>%s</texture>%s%s</control>' % (
                left, top, width, height, extra, '', texture,
                '<colordiffuse>%s</colordiffuse>' % color if color else '',
                '<visible>%s</visible>' % visible if visible else ''))


def review_card(cid, focused):
    # 3.28.126: wider and taller (680 x 236), three whole lines of the review,
    # the name scrolls when focused instead of running into the rating
    w, h = 680, 236
    body = [CARD.format(w=w, h=h)]
    if focused:
        body.insert(0, GLOW.format(gl=-26, gt=-26, gw=w + 52, gh=h + 52, cid=cid))
        body.append(_image(0, 24, 4, 52, 'dexhub/white.png', '$VAR[DhAccent]', 'Control.HasFocus(%d)' % cid))
    write = KIND % 'write'
    rated = '!String.IsEmpty(ListItem.Property(dhs.rating))'
    spoiler = '!String.IsEmpty(ListItem.Property(dhs.spoiler))'
    scroll = '<scroll>true</scroll>' if focused else ''
    body += [
        _image(24, 22, 56, 56, 'dexhub/circle.png', '$VAR[DhAccent]'),
        _label(24, 22, 56, 56, 'font14', 'FFFFFFFF', '[B]$INFO[ListItem.Property(dhs.initial)][/B]', 'center'),
        _label(98, 20, 420, 32, 'font12', 'FFFFFFFF', '[B]$INFO[ListItem.Label][/B]', extra=scroll),
        _label(98, 50, 420, 28, 'font10', '99FFFFFF',
               '$INFO[ListItem.Property(dhs.src)]$INFO[ListItem.Property(dhs.date),  ·  ]', visible='!' + write),
        _label(98, 50, 556, 28, 'font10', '99FFFFFF', '$INFO[ListItem.Label2]', visible=write),
        _image(552, 28, 22, 22, 'dexhub/star.png', 'FFFFC24A', rated),
        _label(578, 22, 80, 34, 'font12', 'FFFFFFFF', '[B]$INFO[ListItem.Property(dhs.rating)][/B]/10', visible=rated),
        _image(24, 94, w - 48, 1, 'dexhub/white.png', '14FFFFFF'),
        '<control type="textbox"><left>24</left><top>104</top><width>%d</width><height>90</height><font>font10</font>'
        '<textcolor>D9FFFFFF</textcolor><label>$INFO[ListItem.Label2]</label>'
        '<visible>!%s + String.IsEmpty(ListItem.Property(dhs.spoiler))</visible></control>' % (w - 48, write),
        _image(24, 110, 4, 32, 'dexhub/white.png', '66FFFFFF', spoiler),
        _label(40, 106, w - 64, 40, 'font10', '80FFFFFF', '[I]$INFO[ListItem.Label2][/I]', visible=spoiler),
        _label(24, 198, 300, 28, 'font10', '80FFFFFF', '$INFO[ListItem.Property(dhs.likes)]'),
        # writing one: what it does, under the name
        '<control type="textbox"><left>24</left><top>108</top><width>%d</width><height>90</height><font>font10</font>'
        '<textcolor>%s</textcolor><label>$INFO[ListItem.Property(dhs.hint)]</label><visible>%s</visible></control>' % (
            w - 48, 'E6FFFFFF' if focused else '99FFFFFF', write),
    ]
    inner = ''.join(body)
    zoom = ZOOM.format(cid=cid) if focused else ''
    return ('<control type="group"><left>0</left><top>12</top><width>%d</width><height>%d</height>%s%s</control>'
            % (w, h, zoom, inner))


def artwork_card(cid, focused):
    w, h = 384, 216
    logo, fan, reset = KIND % 'clearlogo', KIND % 'fanart', KIND % 'reset'
    body = [CARD.format(w=w, h=h)]
    if focused:
        body.insert(0, GLOW.format(gl=-26, gt=-26, gw=w + 52, gh=h + 52, cid=cid))
    body += [
        _image(0, 0, w, h, '$INFO[ListItem.Art(thumb)]', visible=fan,
               extra='<aspectratio>scale</aspectratio><fadetime>160</fadetime>').replace(
            '<texture>', '<texture background="true" diffuse="dexhub/mask_landscape.png">'),
        _image(0, 0, w, h, 'dexhub/scrim_bottom.png', 'B3000000', logo),
        _image(36, 30, w - 72, h - 60, '$INFO[ListItem.Art(thumb)]', visible=logo,
               extra='<aspectratio>keep</aspectratio><fadetime>160</fadetime>').replace(
            '<texture>', '<texture background="true">'),
        _image(w // 2 - 36, 52, 72, 72, 'dexhub/lay_reset.png', 'E6FFFFFF', reset),
        _label(16, 134, w - 32, 36, 'font12', 'FFFFFFFF', '[B]$INFO[ListItem.Label][/B]', 'center', reset),
        _image(w - 52, 12, 40, 40, 'dexhub/circle.png', '$VAR[DhAccent]', '!String.IsEmpty(ListItem.Property(dhs.on))'),
        _image(w - 46, 18, 28, 28, 'dexhub/lay_done.png', 'FFFFFFFF', '!String.IsEmpty(ListItem.Property(dhs.on))'),
    ]
    inner = ''.join(body)
    zoom = ZOOM.format(cid=cid) if focused else ''
    caption = _label(0, h + 20, w, 30, 'font10', 'FFFFFFFF' if focused else '99FFFFFF',
                     '[B]$INFO[ListItem.Label][/B]$INFO[ListItem.Label2,  ·  ]', 'center', '!' + reset)
    caption2 = _label(0, h + 20, w, 30, 'font10', 'FFFFFFFF' if focused else '99FFFFFF',
                      '$INFO[ListItem.Label2]', 'center', reset)
    return ('<control type="group"><left>0</left><top>12</top><width>%d</width><height>%d</height>%s%s%s%s</control>'
            % (w, h + 60, zoom, inner, caption, caption2))


def section(group_id, cid, name, var, item_w, item_h, card, list_h):
    heading = _label(96, 4, 1400, 44, 'font14', 'FFFFFFFF',
                     '[B]$INFO[Window(Home).Property(dhs.tp.h.%s)][/B]' % name,
                     visible='Integer.IsGreater(Container(%d).NumItems,0)' % cid)
    lst = ('<control type="list" id="%d"><left>84</left><top>50</top><width>1836</width><height>%d</height>'
           '<orientation>horizontal</orientation><scrolltime tween="cubic" easing="out">220</scrolltime>%s'
           '<itemlayout width="%d" height="%d">%s</itemlayout><focusedlayout width="%d" height="%d">%s</focusedlayout>'
           '<content sortby="playlist">$VAR[%s]</content>%s%s</control>' % (
               cid, list_h, ONCLICK, item_w, item_h, card(cid, False), item_w, item_h, card(cid, True), var,
               chain_for(cid, 'ondown', 'noop'), chain_for(cid, 'onup', '8101')))
    # the row comes in once it has cards (it stays laid out while they load, as Cast does)
    motion = ''.join('<animation effect="%s" time="%d" tween="cubic" easing="out" '
                     'condition="Integer.IsGreater(Container(%d).NumItems,0)">Conditional</animation>' % (e, ms, cid)
                     for e, ms in (('fade" start="0" end="100', 240), ('slide" start="0,40" end="0,0', 300)))
    return ('<control type="group" id="%d"><height>%d</height>%s'
            '<visible>!String.IsEqual(Window(Home).Property(dhs.tp.none.%s),ListItem.Property(dhs.id))</visible>'
            '%s%s</control>' % (group_id, list_h + 60, motion, name, heading, lst))


def _end_of(text, start):
    depth = 0
    for m in re.finditer(r'<control\b|</control>', text[start:]):
        depth += -1 if m.group(0) == '</control>' else 1
        if depth == 0:
            return start + m.end()
    raise ValueError('unclosed control')


# ------------------------------------------------------------------ files
def _staged_path(base, var, cid, action, rev, before_cid, before_name):
    """v5.10.413: a row that loads once the row above it has: Reviews after
    Cast, Pictures after Reviews (each as soon as the one above has cards
    or has nothing), so they never compete with the top of the page; kept
    while its cards are this title's."""
    above = ('[Integer.IsGreater(Container(%d).NumItems,0) | '
             'String.IsEqual(Window(Home).Property(dhs.tp.none.%s),ListItem.Property(dhs.id))]' % (before_cid, before_name))
    mine = ('[Integer.IsGreater(Container(%d).NumItems,0) + '
            'String.IsEqual(Container(%d).ListItem.Property(dhs.tp.owner),ListItem.Property(dhs.id))]' % (cid, cid))
    ready = '[String.IsEqual(Window(Home).Property(dhs.tp.id),ListItem.Property(dhs.id)) + %s]' % above
    return ('<variable name="%s"><value condition="%s | %s">plugin://%s/?action=%s&amp;rev='
            '$INFO[Window(Home).Property(dhs.tp.rev.%s)]&amp;$INFO[ListItem.Property(dhs.q)]</value>'
            '<value>plugin://%s/?action=skin_empty</value></variable>' % (var, ready, mine, base, action, rev, base))


def patch_includes(text):
    """(text, changed): the two rows' paths, written as this script writes
    them now (a skin that has older ones gets them replaced)."""
    m = re.search(r'<variable name="DhsTpCastPath">.*?</variable>', text, re.S)
    if not m:
        raise ValueError('no DhsTpCastPath')
    base = re.search(r'plugin://([^/]+)/\?action=skin_cast', m.group(0)).group(1)
    rows = (_staged_path(base, 'DhsTpReviewsPath', REVIEWS, 'skin_reviews', 'reviews', 8400, 'cast')
            + _staged_path(base, 'DhsTpArtworkPath', ARTWORK, 'skin_artwork', 'artwork', REVIEWS, 'reviews'))
    old = re.compile(r'<variable name="DhsTp(?:Reviews|Artwork)Path">.*?</variable>', re.S)
    stripped = old.sub('', text)
    m = re.search(r'<variable name="DhsTpCastPath">.*?</variable>', stripped, re.S)
    new = stripped[:m.end()] + rows + stripped[m.end():]
    return new, new != text


def _rows():
    return (section(8808, REVIEWS, 'reviews', 'DhsTpReviewsPath', 704, 266, review_card, 260)
            + section(8809, ARTWORK, 'artwork', 'DhsTpArtworkPath', 408, 300, artwork_card, 290))


def patch_dialog(text, problems):
    """(text, chains changed); a skin with the rows gets them drawn again
    as this script draws them now (their cards change between releases)."""
    if 'id="%d"' % REVIEWS in text:
        start = text.find('<control type="group" id="8808"')
        end = _end_of(text, text.find('<control type="group" id="8809"'))
        if start < 0 or end <= start:
            raise ValueError('the rows are there but not as this script writes them')
        return text[:start] + _rows() + text[end:], 0
    text, changed = patch_chains(text, 'DialogVideoInfo.xml', problems)
    load = '<onload>ClearProperty(dhs.tp.none.cast,home)</onload>'
    if load not in text:
        raise ValueError('no cast onload')
    text = text.replace(load, load + ''.join('<onload>ClearProperty(dhs.tp.none.%s,home)</onload>' % n
                                             for n in ('reviews', 'artwork')), 1)
    start = text.find('<control type="group" id="8804"')
    if start < 0:
        raise ValueError('no Cast section (group 8804)')
    end = _end_of(text, start)
    return text[:end] + _rows() + text[end:], changed


# ------------------------------------------------------------------ the island
def _rx(old):
    """old as a pattern that a skin with or without whitespace between its tags matches"""
    return re.compile(r'>\s*<'.join(re.escape(part) for part in re.split(r'>\s*<', old)))


def _one(text, old, new, what):
    found = list(_rx(old).finditer(text))
    if len(found) != 1:
        raise ValueError('island: %s found %d times' % (what, len(found)))
    return text[:found[0].start()] + new + text[found[0].end():]


def _tab(text, left, width, new_left, new_width, label_w):
    """A tab's group, moved and narrowed (its pill, button and count)."""
    head = '<control type="group"><left>%d</left><top>66</top><width>%d</width><height>52</height>' % (left, width)
    found = list(_rx(head).finditer(text))
    if len(found) != 1:
        raise ValueError('island: tab at %d' % left)
    start = found[0].start()
    end = _end_of(text, start)
    group = text[start:end]
    group = _rx(head).sub('<control type="group"><left>%d</left><top>66</top><width>%d</width><height>52</height>'
                          % (new_left, new_width), group, count=1)
    group = group.replace('<width>%d</width>' % width, '<width>%d</width>' % new_width)
    group = re.sub(r'(<left>54</left>\s*<top>0</top>\s*<width>)\d+(</width>)', r'\g<1>%d\2' % label_w, group)
    group = re.sub(r'<left>80</left>(\s*<top>8</top>)', r'<left>%d</left>\1' % (new_width - 20), group)
    return text[:start] + group + text[end:]


FRIENDS = 'String.IsEqual(Skin.String(DexHub.PanelTab),friends)'


def friends_tab():
    on = '$EXP[dh_panel_friends]'
    return ('<control type="group"><left>640</left><top>66</top><width>70</width><height>52</height>'
            '<control type="image"><left>0</left><top>0</top><width>70</width><height>52</height><texture border="26" colordiffuse="14FFFFFF">dexhub/pill.png</texture><visible>!%(on)s</visible></control>'
            '<control type="image"><left>0</left><top>0</top><width>70</width><height>52</height><texture border="26" colordiffuse="$VAR[DhKodiFocus]">dexhub/pill.png</texture><visible>%(on)s</visible></control>'
            '<control type="button" id="9006"><left>0</left><top>0</top><width>70</width><height>52</height><texturefocus border="26" colordiffuse="E6FFFFFF">dexhub/osd/legacy/island_ring.png</texturefocus><texturenofocus/><label/>'
            '<onfocus>Skin.SetString(DexHub.PanelTab,friends)</onfocus><onleft>9005</onleft><onright>9004</onright><ondown>9022</ondown><onup>noop</onup></control>'
            '<control type="image"><left>12</left><top>13</top><width>26</width><height>26</height><texture colordiffuse="$VAR[DhKodiOnFocus]">dexhub/osd/legacy/users.png</texture><visible>%(on)s</visible></control>'
            '<control type="image"><left>12</left><top>13</top><width>26</width><height>26</height><texture colordiffuse="D9FFFFFF">dexhub/osd/legacy/users.png</texture><visible>!%(on)s</visible></control>'
            '<control type="label"><left>42</left><top>0</top><width>26</width><height>52</height><font>dh_small_b</font><textcolor>$VAR[DhKodiOnFocus]</textcolor><align>left</align><aligny>center</aligny><label>[B]$INFO[Window(Home).Property(dhs.panel.n.friends)][/B]</label><visible>%(on)s</visible></control>'
            '<control type="label"><left>42</left><top>0</top><width>26</width><height>52</height><font>dh_small_b</font><textcolor>99FFFFFF</textcolor><align>left</align><aligny>center</aligny><label>[B]$INFO[Window(Home).Property(dhs.panel.n.friends)][/B]</label><visible>!%(on)s</visible></control>'
            '</control>' % {'on': on})


def users_icon(path):
    """The Friends tab's icon, drawn as the panel's other line icons (128 px,
    white strokes): two people, the second behind the first."""
    from PIL import Image, ImageDraw
    k = 8                                  # drawn large, then made small (smooth edges)
    big = Image.new('RGBA', (128 * k, 128 * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    w = 10 * k
    white = (255, 255, 255, 255)
    d.ellipse([22 * k, 22 * k, 62 * k, 62 * k], outline=white, width=w)              # the first's head
    d.arc([6 * k, 72 * k, 78 * k, 144 * k], 180, 360, fill=white, width=w)           # and shoulders
    d.arc([66 * k, 24 * k, 98 * k, 56 * k], 270, 90, fill=white, width=w)            # the second, half hidden
    d.arc([82 * k, 74 * k, 124 * k, 144 * k], 270, 360, fill=white, width=w)
    small = big.resize((128, 128), Image.LANCZOS)
    small.save(path)


def patch_panel(text, includes, base):
    """(island xml, includes) with the Friends tab; unchanged when it is there."""
    if 'id="9006"' in text:
        return text, includes, False
    t = re.sub(r'(<control type="label">\s*<left>48</left>\s*<top>64</top>\s*<width>)208(</width>)', r'\g<1>196\2', text, count=1)
    t = _tab(t, 270, 112, 252, 100, 42)
    t = _tab(t, 388, 100, 358, 88, 30)
    t = _tab(t, 494, 100, 452, 88, 30)
    t = _one(t, '<control type="group"><left>600</left><top>66</top><width>100</width><height>52</height><control type="button" id="9005"><width>100</width>',
             '<control type="group"><left>546</left><top>66</top><width>88</width><height>52</height><control type="button" id="9005"><width>88</width>', 'Monitoring tab')
    t = _one(t, '<onfocus>Skin.SetString(DexHub.PanelTab,monitored)</onfocus><onleft>9003</onleft><onright>9004</onright>',
             '<onfocus>Skin.SetString(DexHub.PanelTab,monitored)</onfocus><onleft>9003</onleft><onright>9006</onright>', 'Monitoring right')
    pencil = '<control type="group"><left>710</left><top>66</top><width>52</width><height>52</height>'
    t = _one(t, pencil, friends_tab() + pencil.replace('<left>710</left>', '<left>716</left>'), 'pencil')
    t = _one(t, '<onleft>9005</onleft><onright>9001</onright>', '<onleft>9006</onleft><onright>9001</onright>', 'pencil left')
    # the buttons under the tabs: Refresh in place of Manage
    manage = list(_rx('<control type="button" id="9020"><width>338</width><height>48</height>').finditer(t))
    if len(manage) != 1:
        raise ValueError('island: Manage button')
    j = t.find('</control>', manage[0].start()) + len('</control>')
    t = t[:j - len('</control>')] + '<visible>!$EXP[dh_panel_friends]</visible></control>' + (
        '<control type="button" id="9022"><width>338</width><height>48</height><font>dh_small</font>'
        '<label>$INFO[Window(Home).Property(dhs.panel.label.refresh)]</label><textcolor>CCFFFFFF</textcolor>'
        '<focusedcolor>$VAR[DhKodiOnFocus]</focusedcolor><texturefocus border="24" colordiffuse="$VAR[DhKodiFocus]">dexhub/pill.png</texturefocus>'
        '<texturenofocus border="24" colordiffuse="14FFFFFF">dexhub/pill.png</texturenofocus><visible>$EXP[dh_panel_friends]</visible>'
        '<onup>9006</onup><ondown condition="Integer.IsGreater(Container(9100).NumItems,0)">9100</ondown><ondown>noop</ondown>'
        '<onclick>RunPlugin(plugin://%s/?action=skin_panel_refresh)</onclick></control>' % base) + t[j:]
    t = _one(t, '<onup condition="$EXP[dh_panel_saved]">9011</onup><onup>9020</onup>',
             '<onup condition="$EXP[dh_panel_saved]">9011</onup><onup condition="$EXP[dh_panel_friends]">9022</onup><onup>9020</onup>', 'list up')
    # the rows' icon on the right and the empty tab's picture
    t, n = re.subn(r'(<control type="image">\s*<left>666</left>\s*<top>50</top>\s*<width>32</width>\s*<height>32</height>\s*'
                   r'<texture colordiffuse="[^"]*">)dexhub/osd/legacy/play-f.png(</texture>)\s*<visible>\$EXP\[dh_panel_alerts\]</visible>\s*</control>',
                   lambda m: m.group(0) + m.group(1) + 'dexhub/osd/legacy/users.png' + m.group(2)
                   + '<visible>$EXP[dh_panel_friends]</visible></control>', t)
    if n != 2:
        raise ValueError('island: row icons found %d times' % n)
    empty = ('<control type="image"><left>352</left><top>420</top><width>72</width><height>72</height><texture colordiffuse="40FFFFFF">'
             'dexhub/osd/legacy/calendar.png</texture><visible>$EXP[dh_panel_schedule] + [!Integer.IsGreater(Container(9100).NumItems,0) + !Container(9100).IsUpdating]</visible></control>')
    t = _one(t, empty, empty + empty.replace('calendar.png', 'group.png').replace('dh_panel_schedule', 'dh_panel_friends'), 'empty picture')
    load = '<onload condition="String.IsEqual(Skin.String(DexHub.PanelTab),alerts)">SetFocus(9002)</onload>'
    t = _one(t, load, load + '<onload condition="%s">SetFocus(9006)</onload>' % FRIENDS, 'onload')

    inc = _one(includes, '<expression name="dh_panel_monitored">',
               '<expression name="dh_panel_friends">%s</expression><expression name="dh_panel_monitored">' % FRIENDS, 'expression')
    for var, value in (('DhPanelKick', '$INFO[Window(Home).Property(dhs.panel.kick.friends)]'),
                       ('DhPanelTitle', '$INFO[Window(Home).Property(dhs.panel.label.friends)]'),
                       ('DhPanelEmpty', '$INFO[Window(Home).Property(dhs.panel.empty.friends)]')):
        inc = _one(inc, '<variable name="%s">' % var,
                   '<variable name="%s"><value condition="%s">%s</value>' % (var, FRIENDS, value), var)
    return t, inc, True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('skin')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    xml = os.path.join(args.skin, 'xml')
    problems, files = [], {}
    for name in ('Includes_DexHub.xml', 'DialogVideoInfo.xml', 'Includes_DexDesigns.xml', 'Custom_1195_DexPanel.xml'):
        files[name] = open(os.path.join(xml, name), encoding='utf-8').read()
    new = dict(files)
    new['Includes_DexHub.xml'], vars_added = patch_includes(files['Includes_DexHub.xml'])
    new['DialogVideoInfo.xml'], n_dialog = patch_dialog(files['DialogVideoInfo.xml'], problems)
    n_designs = 0
    if 'id="%d"' % REVIEWS not in files['DialogVideoInfo.xml']:
        new['Includes_DexDesigns.xml'], n_designs = patch_chains(files['Includes_DexDesigns.xml'],
                                                                 'Includes_DexDesigns.xml', problems)
    base = re.search(r'plugin://([^/]+)/\?action=skin_cast', files['Includes_DexHub.xml']).group(1)
    icon = os.path.join(args.skin, 'media', 'dexhub', 'osd', 'legacy', 'users.png')
    new['Custom_1195_DexPanel.xml'], new['Includes_DexHub.xml'], island = patch_panel(
        files['Custom_1195_DexPanel.xml'], new['Includes_DexHub.xml'], base)
    for name, text in new.items():
        ET.fromstring(text)              # still parses
    print('%s: paths %s, %d page chain(s) and %d button chain(s) learned the rows, Friends tab %s'
          % (args.skin, 'written' if vars_added else 'as they are', n_dialog, n_designs,
             'added' if island else 'already there'))
    for p in problems:
        print('  ' + p)
    if problems:
        return 1
    if not args.check:
        if not os.path.exists(icon):
            users_icon(icon)
        for name, text in new.items():
            if text != files[name]:
                with open(os.path.join(xml, name), 'w', encoding='utf-8') as fh:
                    fh.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
