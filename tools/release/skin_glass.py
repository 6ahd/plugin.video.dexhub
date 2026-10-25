#!/usr/bin/env python3
"""Clear Glass and the Apple-style focus as skin options (skin 3.28.141).

    python3 tools/release/skin_glass.py SKIN_DIR [--check]

Two switches in the skin's settings, right after the plot scroll one, both
off by default (Skin.ToggleSetting, so a skin that never had them is as it
was):

* Clear Glass (dh.glass): a glass bar behind the Home's top row, and the
  hero buttons that do not have the cursor in glass (the one that has it
  stays white). Each glass image sits beside the original with the opposite
  condition, so with the switch off the Home is exactly as before.
* Apple-style focus (dh.apple): a light edge and a soft shadow around the
  focused card instead of the coloured ring (DhRingColor, DhGlowFocus).

The design came from a session on the Mac (skin.dexhub.tvos 3.28.141); the
images are its own (tools/release/assets/glass_bar.png, glass_pill.png). The
labels are 32152 and 32153 in the skin's strings.po, which Dex Hub publishes
in its own language (skinui/strings.py), as the other settings' labels.

A second run changes nothing; a piece the tool cannot find leaves the files
as they were and is named. --check only reports.
"""
import argparse
import os
import re
import shutil
import sys

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
GLASS, APPLE = 'Skin.HasSetting(dh.glass)', 'Skin.HasSetting(dh.apple)'
LABELS = {
    32152: ('Clear Glass for the buttons and the top bar', 'زجاج شفاف (Clear Glass) للأزرار والشريط العلوي'),
    32153: ('Apple-style focus: a light edge and a soft shadow instead of the coloured ring',
            'تحديد بأسلوب Apple: حافة ضوء خفيفة وظل ناعم بدل الإطار الملوّن'),
}
UNFOCUSED = re.compile(r'(<control type="image">\s*<left>0</left>\s*<top>0</top>\s*<width>270</width>\s*'
                       r'<height>68</height>\s*<texture border="30" colordiffuse="40FFFFFF">dexhub/pill\.png</texture>\s*'
                       r'<visible>)([^<]*)(</visible>\s*</control>)')
TWIN = ('<control type="image"><left>0</left><top>0</top><width>270</width><height>68</height>'
        '<texture border="34" colordiffuse="FFFFFFFF">dexhub/glass_pill.png</texture>'
        '<visible>[%s] + ' + GLASS + '</visible></control>')
BAR = ('<control type="image"><left>48</left><top>22</top><width>1824</width><height>76</height>'
       '<texture border="38">dexhub/glass_bar.png</texture><visible>' + GLASS + '</visible></control>')


def home(text, problems):
    """The hero buttons' glass twins (list 4010) and the bar behind the top row."""
    if 'dexhub/glass_pill.png' in text:
        return text
    start = text.find('<control type="list" id="4010"')
    if start < 0:
        problems.append('Home.xml: no list 4010')
        return text
    end = text.find('</focusedlayout>', start)
    part = text[start:end]
    found = len(UNFOCUSED.findall(part))
    if found != 2:
        problems.append('Home.xml: %d unfocused hero pills in list 4010, 2 expected' % found)
        return text
    part = UNFOCUSED.sub(lambda m: m.group(1) + '[%s] + !%s' % (m.group(2), GLASS) + m.group(3)
                         + TWIN % m.group(2), part)
    text = text[:start] + part + text[end:]
    anchor = '<control type="button" id="9005">'
    if text.count(anchor) != 1:
        problems.append('Home.xml: the top row\'s button 9005 is not there once')
        return text
    return text.replace(anchor, BAR + anchor, 1)


def includes(text, problems):
    """The Apple-style focus: the ring light, the glow a soft shadow."""
    for name, value in (('DhRingColor', '59FFFFFF'), ('DhGlowFocus', '8C000000')):
        head = '<variable name="%s">' % name
        if text.count(head) != 1:
            problems.append('Includes_DexHub.xml: %s is not there once' % name)
            continue
        first = '<value condition="%s">%s</value>' % (APPLE, value)
        if head + first in text:
            continue
        text = text.replace(head, head + first, 1)
    for n in LABELS:
        name = '<variable name="DhSettingsString%d">' % n
        if name not in text:
            var = (name + '<value condition="!String.IsEmpty(Window(Home).Property(dhs.s.%d))">'
                   '$INFO[Window(Home).Property(dhs.s.%d)]</value><value>$LOCALIZE[%d]</value></variable>' % (n, n, n))
            at = text.rfind('</includes>')
            text = text[:at] + var + text[at:]
    return text


def settings(text, problems):
    """The two switches after the plot scroll one (8116)."""
    if 'Skin.ToggleSetting(dh.glass)' in text:
        return text
    m = re.search(r'<control type="radiobutton" id="8116">.*?</control>', text, re.S)
    if not m:
        problems.append('SkinSettings.xml: no plot scroll switch (8116)')
        return text
    new = ''.join('<control type="radiobutton" id="%d"><label>$VAR[DhSettingsString%d]</label>'
                  '<include>DhSettingRadio</include><selected>%s</selected><onclick>Skin.ToggleSetting(%s)</onclick>'
                  '</control>' % (cid, n, cond, cond[len('Skin.HasSetting('):-1])
                  for cid, n, cond in ((8199, 32152, GLASS), (8198, 32153, APPLE)))
    return text[:m.end()] + new + text[m.end():]


def strings(text, lang):
    for n, (en, ar) in LABELS.items():
        if 'msgctxt "#%d"' % n in text:
            continue
        text = text.rstrip('\n') + '\n\nmsgctxt "#%d"\nmsgid "%s"\nmsgstr "%s"\n' % (n, en, ar if lang == 'ar_sa' else '')
    return text


def run(skin, check=False):
    problems, plan = [], {}
    xml = os.path.join(skin, 'xml')
    for name, change in (('Home.xml', home), ('Includes_DexHub.xml', includes), ('SkinSettings.xml', settings)):
        path = os.path.join(xml, name)
        with open(path, encoding='utf-8') as fh:
            old = fh.read()
        new = change(old, problems)
        if new != old:
            plan[path] = new
    for lang in ('en_gb', 'ar_sa'):
        path = os.path.join(skin, 'language', 'resource.language.%s' % lang, 'strings.po')
        with open(path, encoding='utf-8') as fh:
            old = fh.read()
        new = strings(old, lang)
        if new != old:
            plan[path] = new
    if problems:
        print('left as it was: ' + '; '.join(problems))
        return []
    changed = [os.path.relpath(p, skin) for p in plan]
    for name in ('glass_bar.png', 'glass_pill.png'):
        target = os.path.join(skin, 'media', 'dexhub', name)
        if not os.path.exists(target) or open(target, 'rb').read() != open(os.path.join(ASSETS, name), 'rb').read():
            changed.append('media/dexhub/' + name)
            if not check:
                shutil.copyfile(os.path.join(ASSETS, name), target)
    if not check:
        for path, text in plan.items():
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(text)
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
