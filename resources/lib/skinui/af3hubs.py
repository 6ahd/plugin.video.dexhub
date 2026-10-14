from resources.lib.tvos_assets import resolve_assets as _tvos_assets
# -*- coding: utf-8 -*-
"""Dex Hub's own sections in Arctic Fuse 3's top menu (v5.10.120).

Before, Dex Hub put its rows on the skin's Home and took one of the skin's
four hubs (1101 to 1104) for its films and one for its series, only when one
was left empty: on a skin whose four hubs are the user's own there was no
room ("no free hub"), and the Home the user arranged got Dex Hub's rows on
top of it. Now Dex Hub adds hubs of its own after the skin's: Dex Hub (its
Home's rows), its films and its series, each with Dex Hub's mark as its icon
(white, without a background: the skin tints its icons). The skin's Home and
its hubs stay as the user has them.

Arctic Fuse 3 knows its hubs by window id in a few files; the new ones (1982,
1983, 1984) are added next to hub 1104 wherever it is named:

  1080i/Custom_198x_Hub.xml           written: the skin's own hub window
                                      (Custom_1104_Hub.xml) with the new id
  1080i/Includes_Home.xml             the top menu: the hubs' names, icons,
                                      left/right order, the submenus
  1080i/Includes_Hubs.xml             what a hub sets as it opens
  shortcuts/generator/.../home_widgets.xml, home_submenu.xml
                                      script.skinvariables builds the hubs'
                                      rows from Dex Hub's menus
  1080i/Custom_1181_Dialog_Submenu.xml, Custom_1182_Dialog_Topmenu_Overlay.xml,
  Custom_1198_Dialog_Startup.xml, Includes_Background.xml, Home.xml
                                      "is a hub on show" conditions

As with the pages (af3pages.py), every piece of text put in sits between
comments of its own (dexhub-hubs:begin and :end), or is a condition part
naming only the new windows: Remove takes them out and each file is the
text it was. A file whose text is not where this expects it is left alone
(an optional part is skipped; without the required ones no hub goes in).
"""
import os
import re
import xml.etree.ElementTree as ET

import xbmc

from . import common as C
from .af3patch import PatchError, _read, _skin_root, _write, file_sha1, sha1, skin_version

VERSION = 1
MARK = '<!-- dexhub-hubs v%d -->' % VERSION
MARK_PREFIX = '<!-- dexhub-hubs '
BEGIN = '<!--dexhub-hubs:begin-->'
END = '<!--dexhub-hubs:end-->'
_CHUNK = re.compile(re.escape(BEGIN) + '.*?' + re.escape(END), re.S)

# (window, Dex Hub tab, the id the top menu's controls take, icon)
HUBS = (('1982', 'all', '82', 'dexhub_section.png'),
        ('1983', 'movie', '83', 'dexhub_section_movies.png'),
        ('1984', 'series', '84', 'dexhub_section_series.png'))
WINDOWS = tuple(hub[0] for hub in HUBS)
WINDOW_OF = dict((hub[1], hub[0]) for hub in HUBS)
ICON_DIR = _tvos_assets('special://home/addons/plugin.video.dexhub/resources/media/af3/')
# the new windows in a skin condition that lists the hubs
INS = ''.join(' | Window.IsVisible(%s)' % w for w in WINDOWS)
# TMDb Helper's list of the skin's windows (Home.xml)
TMDBH_INS = ''.join('|1%s' % w for w in WINDOWS)
THE_HUB = '1104'            # the skin's last hub: the new ones follow it
_OURS = re.compile(r'Window\.IsVisible\(198[2-4]\)|HomeSwitcher\.198[2-4]|198[2-4](?:widgets|submenu)|'
                   r'Home_Icon_198[2-4]|Home_ControlList_Item_198[2-4]')


def icon(window):
    for hub in HUBS:
        if hub[0] == window:
            return ICON_DIR + hub[3]
    return ICON_DIR + HUBS[0][3]


def _chunk(text):
    return BEGIN + text + END


def unpatch_text(text):
    """``text`` without the hubs, exactly as it was; None when something of
    them would be left."""
    new = _CHUNK.sub('', text).replace(INS, '').replace(TMDBH_INS, '')
    if 'dexhub-hubs' in new or _OURS.search(new):
        return None
    return new


def _after(text, pattern, make):
    """``text`` with ``make(block)`` put after every block matching
    ``pattern``; (text, number of blocks)."""
    out, pos, count = [], 0, 0
    for found in re.finditer(pattern, text, re.S):
        block = found.group(0)
        # the block's own indentation, for the copies
        line = text.rfind('\n', 0, found.start())
        indent = text[line + 1:found.start()] if line >= 0 else ''
        if indent.strip():
            indent = ''
        out.append(text[pos:found.end()])
        out.append(_chunk(''.join('\n' + indent + piece for piece in make(block))))
        pos = found.end()
        count += 1
    out.append(text[pos:])
    return ''.join(out), count


def _each(block, ids=True):
    """The skin's hub 1104 block as one copy per new hub."""
    out = []
    for window, _tab, short, _icon in HUBS:
        piece = block.replace(THE_HUB, window)
        if ids:
            piece = re.sub(r'(<param name="id">)04(</param>)', r'\g<1>%s\2' % short, piece)
        out.append(piece)
    return out


def _need(count, what):
    if not count:
        raise PatchError('%s is not as expected' % what)


# --------------------------------------------------------------------------
# the edits, one per file
# --------------------------------------------------------------------------

def _home(text):
    """Includes_Home.xml: the top menu."""
    # the icons (the user may set another one in the skin's menu editor)
    def icons(_block):
        return ['<variable name="Home_Icon_%s">'
                '<value condition="!String.IsEmpty(Skin.String(HomeSwitcher.%s.Icon))">'
                '$INFO[Skin.String(HomeSwitcher.%s.Icon)]</value><value>%s</value></variable>'
                % (window, window, window, ICON_DIR + name) for window, _tab, _short, name in HUBS]
    text, count = _after(text, r'<variable name="Home_Icon_1104">.*?</variable>', icons)
    _need(count, 'the hub icons')

    # the hidden list that orders the hubs for left and right: each hub's
    # item shows "on show is this hub or one before it"
    def items(block):
        chain = re.search(r'<param name="visible">(.*?)</param>', block, re.S)
        if not chain or not chain.group(1).rstrip().endswith('Window.IsVisible(1104)'):
            raise PatchError('the hub order is not as expected')
        out = []
        before = ''
        for window, _tab, _short, _icon in HUBS:
            before += ' | Window.IsVisible(%s)' % window
            piece = block.replace(chain.group(0), '\0')
            piece = piece.replace(THE_HUB, window)
            piece = piece.replace('\0', '<param name="visible">%s%s</param>' % (chain.group(1), before))
            out.append(piece)
        return out
    text, count = _after(text, r'<include name="Home_ControlList_Item_1104">.*?</include>\s*</include>', items)
    _need(count, 'the hub order')
    text, count = _after(text, r'<include content="Home_ControlList_Item_1104">\s*'
                               r'<param name="mod">\$PARAM\[mod\]</param>\s*</include>', _each)
    _need(count, 'the hub order list')
    # the sections after the hubs (next aired, TV, add-ons) count the new hubs as before them
    text, count = re.subn(r'Window\.IsVisible\(1104\)(?= \| Window\.IsVisible\(110[6-9]\))',
                          'Window.IsVisible(1104)' + INS.replace('\\', '\\\\'), text)
    # the names (or icons) along the top, and the icons down the side
    text, count = _after(text, r'<include content="Home_Object" condition="!String\.IsEmpty\(Skin\.String\('
                               r'HomeSwitcher\.1104\.Toggle\)\)">.*?</include>', _each)
    _need(count, 'the top menu')
    text, _count = _after(text, r'<include content="Home_Object_VertIcon" condition="!String\.IsEmpty\('
                                r'Skin\.String\(HomeSwitcher\.1104\.Toggle\)\)">.*?</include>', _each)
    # the submenus
    text, _count = _after(text, r'<include content="Home_Submenu_Start"><param name="id">04</param></include>',
                          _each)
    text, _count = _after(text, r'<include content="Home_Submenu_Panel" condition="!String\.IsEmpty\('
                                r'Skin\.String\(HomeSwitcher\.1104\.Toggle\)\)">.*?</include>', _each)
    return text


def _hubs_onload(text):
    text, count = _after(text, r'<include content="Hub_Onload_Window">\s*<param name="window">1104</param>\s*'
                               r'</include>', lambda block: _each(block, ids=False))
    _need(count, "the hubs' onload")
    return text


def _generator(name):
    def edit(text):
        text, count = _after(text, r'<list name="1104%s">\s*<value name="window_id">1104</value>\s*</list>' % name,
                             lambda block: _each(block, ids=False))
        _need(count, 'the %s lists' % name)
        return text
    return edit


def _submenu_dialog(text):
    text, count = _after(text, r'<onload condition="Window\.IsVisible\(1104\)">SetFocus\(7004\)</onload>',
                         lambda block: ['<onload condition="Window.IsVisible(%s)">SetFocus(70%s)</onload>'
                                        % (window, short) for window, _tab, short, _icon in HUBS])
    _need(count, 'the submenu dialog')
    text, count = re.subn(r'Window\.IsVisible\(1104\)(?=\])', 'Window.IsVisible(1104)' + INS, text)
    _need(count, 'the submenu dialog')
    return text


def _visible(text):
    text, count = re.subn(r'Window\.IsVisible\(1104\)', 'Window.IsVisible(1104)' + INS, text)
    _need(count, 'a hub condition')
    return text


def _tmdbh(text):
    text, count = re.subn(r'(UseLocalWindowIDs,[0-9|]*11104)', r'\g<1>' + TMDBH_INS, text)
    _need(count, "TMDb Helper's windows")
    return text


# (file, required, edit)
EDITS = (
    ('1080i/Includes_Home.xml', True, _home),
    ('1080i/Includes_Hubs.xml', True, _hubs_onload),
    ('shortcuts/generator/data/base/home_widgets.xml', True, _generator('widgets')),
    ('shortcuts/generator/data/base/home_submenu.xml', False, _generator('submenu')),
    ('1080i/Custom_1181_Dialog_Submenu.xml', False, _submenu_dialog),
    ('1080i/Custom_1182_Dialog_Topmenu_Overlay.xml', False, _visible),
    ('1080i/Custom_1198_Dialog_Startup.xml', False, _visible),
    ('1080i/Includes_Background.xml', False, _visible),
    ('1080i/Home.xml', False, _tmdbh),
)
SOURCE_WINDOW = '1080i/Custom_1104_Hub.xml'


def window_file(window):
    return '1080i/Custom_%s_Hub.xml' % window


def _check(old, new, rel):
    try:
        ET.fromstring(old.encode('utf-8'))
    except Exception:
        return
    try:
        ET.fromstring(new.encode('utf-8'))
    except Exception as exc:
        raise PatchError('%s would not be valid XML (%s)' % (rel, exc))


def _window_text(source, window):
    text = source.replace(THE_HUB, window)
    head = text.find('?>')
    if text.lstrip().startswith('<?xml') and head >= 0:
        return text[:head + 2] + '\n' + MARK + text[head + 2:]
    return MARK + '\n' + text


def _plan(root):
    """({rel: (old, new)}, {rel: text} of the hub windows, [skipped notes]).
    PatchError when the hubs cannot go in."""
    texts, skipped = {}, []
    for rel, required, edit in EDITS:
        path = os.path.join(root, *rel.split('/'))
        try:
            old = _read(path)
        except Exception:
            if required:
                raise PatchError('%s is missing' % rel)
            skipped.append('%s: missing' % rel)
            continue
        if BEGIN in old or INS in old:
            raise PatchError('%s has the hubs already' % rel)
        try:
            new = edit(old)
            _check(old, new, rel)
            if unpatch_text(new) != old:
                raise PatchError('%s: the hubs would not come out of it exactly' % rel)
        except PatchError as exc:
            if required:
                raise
            skipped.append('%s: %s' % (rel, exc))
            continue
        texts[rel] = (old, new)
    try:
        source = _read(os.path.join(root, *SOURCE_WINDOW.split('/')))
    except Exception:
        raise PatchError('%s is missing' % SOURCE_WINDOW)
    if 'Hub_Window' not in source:
        raise PatchError("the skin's hub window is not as expected")
    windows = {}
    folder = os.path.join(root, '1080i')
    try:
        names = os.listdir(folder)
    except Exception:
        names = []
    for window in WINDOWS:
        rel = window_file(window)
        taken = [n for n in names if n.lower().startswith('custom_%s_' % window) and n != rel.split('/')[-1]]
        if taken:
            raise PatchError('the skin has a window %s of its own (%s)' % (window, taken[0]))
        dest = os.path.join(root, *rel.split('/'))
        if os.path.exists(dest) and MARK_PREFIX not in _read(dest):
            raise PatchError('the skin has a %s of its own' % rel)
        windows[rel] = _window_text(source, window)
        _check(source, windows[rel], rel)
    return texts, windows, skipped


def status():
    """'in' (these hubs are in the skin in use), 'old' (another version),
    'ready' (they can go in), 'no' (this skin's text differs)."""
    try:
        root = _skin_root()
        text = _read(os.path.join(root, *EDITS[0][0].split('/')))
    except Exception:
        return 'no', EDITS[0][0]
    if BEGIN in text:
        present = [w for w in WINDOWS if os.path.exists(os.path.join(root, *window_file(w).split('/')))]
        try:
            marks = [MARK in _read(os.path.join(root, *window_file(w).split('/'))) for w in present]
        except Exception:
            marks = []
        if len(present) == len(WINDOWS) and all(marks):
            return 'in', ''
        return 'old', 'windows %s' % present
    try:
        _plan(root)
    except PatchError as exc:
        return 'no', str(exc)
    return 'ready', ''


def apply(backup_dir, record):
    """The hubs into the skin in use (the originals of the files it changes
    copied into <backup_dir>/skin/ first). Returns the parts skipped."""
    root = _skin_root()
    texts, windows, skipped = _plan(root)
    files = record.setdefault('files', {})
    added = record.setdefault('added', {})
    for rel, text in windows.items():
        path = os.path.join(root, *rel.split('/'))
        _write(path, text)
        added[rel] = sha1(text)
    for rel, (old, new) in texts.items():
        keep = os.path.join(backup_dir, 'skin', *rel.split('/'))
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        if not os.path.exists(keep) or file_sha1(keep) != sha1(old):
            with open(keep, 'w', encoding='utf-8') as handle:
                handle.write(old)
        _write(os.path.join(root, *rel.split('/')), new)
        files[rel] = {'orig': sha1(old), 'patched': sha1(new)}
    record['version'] = VERSION
    record['skin_version'] = skin_version()
    record['skipped'] = skipped
    C.log('Arctic Fuse 3: Dex Hub sections %s in (%s)%s' % (
        ', '.join(WINDOWS), ', '.join(sorted(list(texts) + list(windows))),
        ('; skipped: %s' % '; '.join(skipped)) if skipped else ''))
    return skipped


def revert(backup_dir, record):
    """The hubs out of the skin in use: their pieces out of each file (the
    backup when a file's pieces cannot be told apart, only when it is the
    very file this wrote), the hub windows deleted."""
    import shutil
    root = _skin_root()
    done = []
    files = record.get('files') or {}
    for rel in sorted(set(list(files) + [edit[0] for edit in EDITS])):
        path = os.path.join(root, *rel.split('/'))
        try:
            text = _read(path)
        except Exception:
            continue
        if BEGIN not in text and INS not in text and TMDBH_INS not in text:
            continue
        original = unpatch_text(text)
        keep = os.path.join(backup_dir, 'skin', *rel.split('/'))
        if original is not None:
            _write(path, original)
            done.append(rel)
        elif os.path.exists(keep) and file_sha1(path) == (files.get(rel) or {}).get('patched'):
            shutil.copyfile(keep, path)
            done.append(rel)
        else:
            C.log('Arctic Fuse 3: %s keeps pieces of the sections it cannot take out' % rel, xbmc.LOGWARNING)
    for window in WINDOWS:
        rel = window_file(window)
        path = os.path.join(root, *rel.split('/'))
        try:
            if os.path.exists(path) and MARK_PREFIX in _read(path):
                os.remove(path)
                done.append(rel)
        except Exception as exc:
            C.log('Arctic Fuse 3: %s not removed: %s' % (rel, exc), xbmc.LOGWARNING)
    record.clear()
    if done:
        C.log('Arctic Fuse 3: Dex Hub sections taken out of %s' % ', '.join(sorted(done)))
    return done


def ready():
    """Kodi has the new hub windows (the skin was reloaded with them)."""
    try:
        import xbmcgui
        xbmcgui.Window(int(WINDOWS[0]))
        return True
    except Exception:
        return False
