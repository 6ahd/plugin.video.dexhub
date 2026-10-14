# -*- coding: utf-8 -*-
"""The trailer patch for Arctic Fuse 3 (v5.10.112).

Arctic Fuse 3 plays a trailer only when it is asked for (its trailer
dialog). Dex Hub's own Home plays the trailer of the title on show behind its
billboard after a few seconds, and the artwork moves slowly meanwhile. The
patch gives the skin's hubs the same for Dex Hub's titles:

  1080i/Includes_Background.xml  the trailer inside the artwork's frame (the
                                 skin's own "flixart" video frame) while a Dex
                                 Hub trailer plays, any other video as before;
                                 the spotlight's artwork moves slowly (Ken
                                 Burns) once the user rests on a Dex Hub
                                 title (v3: one zoom, then still)
  1080i/Includes_Hubs.xml       the spotlight waits while the trailer plays;
  1080i/Includes_Home.xml        Back, the footer's now playing and the
  1080i/Includes_Furniture.xml   fullscreen shortcut react to what the user
                                 plays, not to a Dex Hub trailer

The service plays the trailer (spotlight.AF3Spotlight, the add-on's trailer
settings). Each file gets a marker; its original is copied into the backup
before the first change and put back by Remove. A skin update brings the
files back without the patch: the service applies it again (af3.Service).
Every edit names the text it changes: a skin whose text differs (another
release, a fork) is left as it is and the patch reports it cannot apply.
"""
import hashlib
import os
import re
import shutil

import xbmc

from . import common as C

VERSION = 3
MARKER = '<!-- dexhub-trailer-patch v%d -->' % VERSION
MARKER_PREFIX = '<!-- dexhub-trailer-patch '
FILES = ('1080i/Includes_Background.xml', '1080i/Includes_Hubs.xml',
         '1080i/Includes_Home.xml', '1080i/Includes_Furniture.xml', '1080i/Includes_Objects.xml')

# v2 (v5.10.114): a Dex Hub collection's animated cover on the focused card
# of a widget, as the add-on's own rows play it (every card shape draws its
# picture through Object_Layout_Image_Rectangle)
_GIF_INCLUDE = '<include name="Object_Layout_Image_Rectangle">'
_GIF_CONTROL = """            <!-- Dex Hub: a collection's animated cover while its card has the focus -->
            <control type="image">
                <texture background="true" diffuse="$PARAM[diffuse]">$INFO[$PARAM[listitem].Art(gif)]</texture>
                <aspectratio scalediffuse="false">scale</aspectratio>
                <visible>$PARAM[selected] + !String.IsEmpty($PARAM[listitem].Art(gif))</visible>
            </control>
"""


def _with_gif(text):
    """Includes_Objects.xml with the cover added to the card picture; None
    when the include is not where the patch expects it."""
    start = text.find(_GIF_INCLUDE)
    if start < 0 or text.count(_GIF_INCLUDE) != 1:
        return None
    end = text.find('</definition>', start)
    nxt = text.find('<include name=', start + len(_GIF_INCLUDE))
    if end < 0 or (0 <= nxt < end):
        return None
    line_start = text.rfind('\n', start, end) + 1
    return text[:line_start] + _GIF_CONTROL + text[line_start:]

TRAILER = '!String.IsEmpty(Window(Home).Property(dexhub.trailer.active))'
_DH_SPOT = '!String.IsEmpty(Container(301).ListItem.Property(dhs.id))'

_VIDEO_OLD = ('            <include condition="![$PARAM[video_background]]">Background_Video_Offscreen</include>\n'
              '            <include condition="$PARAM[video_background]">Background_Video</include>\n')
_VIDEO_NEW = '''            <!-- Dex Hub: its trailer plays inside the artwork's frame; any other video as before -->
            <control type="group">
                <visible>!$EXP[DexHub_Trailer]</visible>
                <include condition="![$PARAM[video_background]]">Background_Video_Offscreen</include>
                <include condition="$PARAM[video_background]">Background_Video</include>
            </control>
            <control type="group">
                <visible>$EXP[DexHub_Trailer]</visible>
                <animation effect="fade" start="0" end="100" time="600" tween="sine" easing="out">Visible</animation>
                <include>Background_Video_FlixArt</include>
            </control>
'''
_ART_OLD = '<texture background="true" diffuse="$PARAM[diffuse]">$VAR[Image_Foreground]</texture>\n'
# v3 (v5.10.130): the artwork moves once the user rests (six seconds without
# a key), zooms once and holds still; a key ends it at once. v1 and v2 zoomed
# for ever (pulse): Arctic Fuse 3 redrew the whole screen at every frame
# while a Dex Hub title was on show, browsing included.
_ART_NEW = ('<texture background="true" diffuse="$PARAM[diffuse]">$VAR[Image_Foreground]</texture>\n'
            '                <!-- Dex Hub: the artwork of its title on show moves slowly once the user rests -->\n'
            '                <animation type="Conditional" condition="$EXP[DexHub_KenBurns]" reversible="false">\n'
            '                    <effect type="zoom" start="100" end="105" center="65%,40%" time="40000" tween="sine" easing="inout" />\n'
            '                </animation>\n')
_ART_NEW_V2 = ('<texture background="true" diffuse="$PARAM[diffuse]">$VAR[Image_Foreground]</texture>\n'
               '                <!-- Dex Hub: the artwork of its title on show moves slowly -->\n'
               '                <animation type="Conditional" condition="$EXP[DexHub_KenBurns]" pulse="true">\n'
               '                    <effect type="zoom" start="100" end="110" center="65%,40%" time="15000" tween="sine" easing="inout" />\n'
               '                </animation>\n')
_SCROLL_OLD = '<param name="condition">[!ControlGroup(310).HasFocus() | System.IdleTime(3)]</param>'
_SCROLL_NEW = ('<param name="condition">[!ControlGroup(310).HasFocus() | System.IdleTime(3)] + '
               'String.IsEmpty(Window(Home).Property(dexhub.trailer.active))</param>')


class PatchError(Exception):
    pass


def _skin_root():
    import xbmcvfs
    return xbmcvfs.translatePath('special://skin/')


def _read(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return handle.read()


def _write(path, text):
    tmp = path + '.dexhub.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        handle.write(text)
    os.replace(tmp, path)


def sha1(text):
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def file_sha1(path):
    try:
        return sha1(_read(path))
    except Exception:
        return ''


def _expressions(root):
    """The expressions the patch adds (only names the skin has are used)."""
    try:
        exps = _read(os.path.join(root, '1080i', 'Includes_Expressions.xml'))
    except Exception:
        exps = ''
    try:
        hubs = _read(os.path.join(root, '1080i', 'Includes_Hubs.xml'))
    except Exception:
        hubs = ''
    ken = [_DH_SPOT, '!Player.HasVideo', '!System.ScreenSaverActive',
           '!String.IsEmpty(Window(Home).Property(dhs.opt.kenburns))', 'System.IdleTime(6)']
    if 'name="Hub_Spotlight_Visible_Expression"' in hubs:
        ken.insert(0, '$EXP[Hub_Spotlight_Visible_Expression]')
    if 'name="Exp_InfoDialogs"' in exps:
        ken.append('!$EXP[Exp_InfoDialogs]')
    return ('    ' + MARKER + '\n'
            '    <expression name="DexHub_Trailer">%s + Player.HasVideo</expression>\n'
            '    <expression name="DexHub_RealMedia">[Player.HasMedia + String.IsEmpty(Window(Home).Property(dexhub.trailer.active))]</expression>\n'
            '    <expression name="DexHub_KenBurns">%s</expression>\n' % (TRAILER, ' + '.join(ken)))


def _patched_texts(root):
    """{relative path: (original text, patched text)} for the files the patch
    changes; PatchError when a required piece of text is not in the skin."""
    out = {}
    path = os.path.join(root, '1080i', 'Includes_Background.xml')
    try:
        text = _read(path)
    except Exception:
        raise PatchError('Includes_Background.xml is missing')
    if MARKER_PREFIX in text:
        raise PatchError('already patched')
    if text.count(_VIDEO_OLD) != 1 or text.count('<includes>') < 1:
        raise PatchError("the skin's background video is not where the patch expects it")
    if 'name="Background_Video_FlixArt"' not in text:
        raise PatchError("the skin has no video frame in its artwork")
    new = text.replace('<includes>', '<includes>\n' + _expressions(root), 1)
    new = new.replace(_VIDEO_OLD, _VIDEO_NEW, 1)
    if new.count(_ART_OLD) == 1:
        new = new.replace(_ART_OLD, _ART_NEW, 1)
    out['1080i/Includes_Background.xml'] = (text, new)
    for rel in ('1080i/Includes_Hubs.xml', '1080i/Includes_Home.xml', '1080i/Includes_Furniture.xml'):
        path = os.path.join(root, *rel.split('/'))
        try:
            text = _read(path)
        except Exception:
            continue
        if MARKER_PREFIX in text:
            raise PatchError('%s already patched' % rel)
        new = text.replace('Player.HasMedia', '$EXP[DexHub_RealMedia]')
        if rel.endswith('Includes_Hubs.xml') and new.count(_SCROLL_OLD) == 1:
            new = new.replace(_SCROLL_OLD, _SCROLL_NEW, 1)
        if new != text:
            new = new.replace('<includes>', '<includes>\n    ' + MARKER, 1)
            out[rel] = (text, new)
    rel = '1080i/Includes_Objects.xml'
    try:
        text = _read(os.path.join(root, *rel.split('/')))
    except Exception:
        text = ''
    if text and MARKER_PREFIX not in text:
        new = _with_gif(text)
        if new:
            out[rel] = (text, new.replace('<includes>', '<includes>\n    ' + MARKER, 1))
    return out


# v5.10.119: the patch taken out by its own text, any version of it (v1 and
# v2 put the same text in): what a skin's file was before it, without a
# backup of that very file (the backup can be of another release of the skin)
_EXPRESSIONS = re.compile(r'<includes>\n    <!-- dexhub-trailer-patch v\d+ -->\n'
                          r'(?:    <expression name="DexHub_\w+">[^\n]*</expression>\n)+')
_MARK_ONLY = re.compile(r'<includes>\n    <!-- dexhub-trailer-patch v\d+ -->')


def unpatch_text(text):
    """``text`` without the trailer patch, or None when something of it
    would be left (a text the patch did not write)."""
    new, count = _EXPRESSIONS.subn('<includes>', text, count=1)
    if not count:
        new = _MARK_ONLY.sub('<includes>', new, count=1)
    for patched, original in ((_VIDEO_NEW, _VIDEO_OLD), (_ART_NEW, _ART_OLD), (_ART_NEW_V2, _ART_OLD),
                              (_SCROLL_NEW, _SCROLL_OLD), (_GIF_CONTROL, '')):
        new = new.replace(patched, original)
    new = new.replace('$EXP[DexHub_RealMedia]', 'Player.HasMedia')
    if 'DexHub_' in new or MARKER_PREFIX in new or 'dexhub.trailer.active' in new:
        return None
    return new


def status():
    """(state, note) of the skin in use: 'patched', 'ready' (can be
    patched), 'no' (this skin's text differs)."""
    try:
        root = _skin_root()
        text = _read(os.path.join(root, '1080i', 'Includes_Background.xml'))
    except Exception:
        return 'no', 'Includes_Background.xml'
    if MARKER in text:
        return 'patched', ''
    if MARKER_PREFIX in text:
        return 'old', ''
    try:
        _patched_texts(root)
    except PatchError as exc:
        return 'no', str(exc)
    return 'ready', ''


def apply(backup_dir, record):
    """Patch the skin in use; the originals are copied into
    <backup_dir>/skin/ first. ``record`` (the state's 'patch' dict) gets each
    file's sha1 before and after. True when the skin is patched."""
    root = _skin_root()
    texts = _patched_texts(root)
    files = record.setdefault('files', {})
    for rel, (old, new) in texts.items():
        keep = os.path.join(backup_dir, 'skin', *rel.split('/'))
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        # the original of this release of the skin (a newer release brings a new one)
        if not os.path.exists(keep) or file_sha1(keep) != sha1(old):
            with open(keep, 'w', encoding='utf-8') as handle:
                handle.write(old)
        _write(os.path.join(root, *rel.split('/')), new)
        files[rel] = {'orig': sha1(old), 'patched': sha1(new)}
    record['version'] = VERSION
    record['skin_version'] = skin_version()
    C.log('Arctic Fuse 3: trailer patch applied to %s' % ', '.join(sorted(texts)))
    return True


def revert(backup_dir, record):
    """Put the originals back where the patch is still in place (a skin
    updated since brought its own files back: those are left alone).

    v5.10.119: the backup only when the file is the very one the patch
    wrote, or the skin is the release the backup was taken of; any other
    file with a patch in (another release, another copy of the skin)
    loses it by its own text (unpatch_text): a backup of another release
    put over it mixed two releases of the skin."""
    root = _skin_root()
    restored = []
    same_release = record.get('skin_version') in ('', None, skin_version())
    files = record.get('files') or {}
    for rel in sorted(set(list(files) + list(FILES))):
        info = files.get(rel) or {}
        path = os.path.join(root, *rel.split('/'))
        keep = os.path.join(backup_dir, 'skin', *rel.split('/'))
        current = file_sha1(path)
        try:
            text = _read(path)
        except Exception:
            text = ''
        if not text:
            continue
        if current and current == info.get('patched') and os.path.exists(keep):
            shutil.copyfile(keep, path)
            restored.append(rel)
        elif (MARKER_PREFIX in text and same_release and os.path.exists(keep)
              and file_sha1(keep) == info.get('orig')):
            shutil.copyfile(keep, path)
            restored.append(rel)
        elif MARKER_PREFIX in text:
            original = unpatch_text(text)
            if original is not None:
                _write(path, original)
                restored.append(rel)
            else:
                C.log('Arctic Fuse 3: %s keeps a trailer patch it cannot take out' % rel, xbmc.LOGWARNING)
    record.clear()
    if restored:
        C.log('Arctic Fuse 3: trailer patch removed from %s' % ', '.join(sorted(restored)))
    return restored


def skin_version():
    try:
        import xbmcaddon
        return xbmcaddon.Addon(xbmc.getSkinDir()).getAddonInfo('version') or ''
    except Exception:
        return ''
