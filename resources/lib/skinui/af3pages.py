# -*- coding: utf-8 -*-
"""Dex Hub's own pages inside Arctic Fuse 3 (v5.10.119, v5.10.120).

skin.dexhub draws Dex Hub's title page and its collection folders as pages
of rows. Whoever keeps Arctic Fuse 3 (its player, its CoreELEC tools, its
fonts) gets the same pages inside it: the add-on carries them
(resources/skins/af3pages, built by the skin's v3/af3pack.py with Arctic
Fuse 3's fonts) and puts them into the skin in use, where Kodi draws them as
it draws the skin's own windows.

  1080i/Custom_1981_DexHubFolder.xml  copied in: the folder page, a window of
                                      its own (folderpage.py opens it)
  1080i/Includes_DexHub.xml           copied in: what the pages use
  1080i/Includes.xml                  names Includes_DexHub.xml
  1080i/DialogVideoInfo.xml           the skin's information dialog: Dex Hub's
                                      title page for Dex Hub's titles (an item
                                      with dhs.id), the skin's own page for
                                      every other title, as before
  1080i/DialogSeekBar.xml             (v1 only) the playing title's logo on
                                      the line over the seek bar; v2
                                      (5.10.120) leaves the skin's line as
                                      it is, and takes v1's out

The pictures stay in the add-on (the pages name them there). Every piece of
text put into a skin file sits between two comments of its own
(dexhub-pages:begin, dexhub-pages:end): Remove takes those pieces out again
and the file is exactly what it was, whatever release of the skin it belongs
to (the original kept in the backup is the last resort). A skin update
brings its own files back and the service puts the pages in again at a quiet
moment (af3.Service); a skin whose text is not where the pages expect it is
left as it is (a part that cannot go in is skipped, the rest goes in).
"""
import os
import re
import xml.etree.ElementTree as ET

import xbmc

from . import common as C
from .af3patch import PatchError, _read, _skin_root, _write, file_sha1, sha1, skin_version

MARKER_PREFIX = '<!-- dexhub-pages '
BEGIN = '<!--dexhub-pages:begin-->'
END = '<!--dexhub-pages:end-->'
_CHUNK = re.compile(re.escape(BEGIN) + '.*?' + re.escape(END), re.S)
OTHER = 'String.IsEmpty(ListItem.Property(dhs.id))'
# v1's seek bar edit (taken out again by revert)
HEADER = '$VAR[Label_OSD_Header]'
OUR_HEADER = '$VAR[DexHub_OSD_Header]'
INCLUDES = '1080i/Includes.xml'
INFO = '1080i/DialogVideoInfo.xml'
SEEKBAR = '1080i/DialogSeekBar.xml'


# --------------------------------------------------------------------------
# the pack (in the add-on)
# --------------------------------------------------------------------------

_PACK = {}


def pack_dir():
    import xbmcaddon
    import xbmcvfs
    path = xbmcvfs.translatePath(xbmcaddon.Addon(C.ADDON_ID).getAddonInfo('path'))
    return os.path.join(path, 'resources', 'skins', 'af3pages')


def pack():
    """pack.json, and the parts as text: {'version', 'marker', 'fold_window',
    'copy', 'parts': {name: text}}; {} when the add-on has no pack."""
    if _PACK.get('data') is None:
        root = pack_dir()
        data = C.read_json(os.path.join(root, 'pack.json'), {}) or {}
        parts = {}
        for name in ('title_onload', 'title_controls'):
            try:
                parts[name] = _read(os.path.join(root, 'parts', name + '.xml'))
            except Exception:
                parts[name] = ''
        if data:
            data['parts'] = parts
            data['root'] = root
        _PACK['data'] = data
    return _PACK['data']


def version():
    return int(pack().get('version') or 0)


def marker():
    return pack().get('marker') or ''


def fold_window():
    return int(pack().get('fold_window') or 1981)


# --------------------------------------------------------------------------
# the edits
# --------------------------------------------------------------------------

def _check(old, new, rel):
    """The edited file parses as XML (when the skin's own did)."""
    try:
        ET.fromstring(old.encode('utf-8'))
    except Exception:
        return                  # Kodi's parser is kinder: nothing to compare with
    try:
        ET.fromstring(new.encode('utf-8'))
    except Exception as exc:
        raise PatchError('%s would not be valid XML (%s)' % (rel, exc))


def _chunk(text):
    return BEGIN + text + END


def unpatch_text(text):
    """``text`` without the pages, exactly as it was before them; None when
    something of them would be left (a text they did not write)."""
    new = _CHUNK.sub('', text).replace(OUR_HEADER, HEADER)
    if 'dexhub-pages' in new or 'DexHub_OSD' in new:
        return None
    return new


def _with_include(text, mark):
    end = text.rfind('</includes>')
    if end < 0:
        raise PatchError("the skin's Includes.xml is not as expected")
    return text[:end] + _chunk('    <include file="Includes_DexHub.xml" /> %s\n' % mark) + text[end:]


_DEFAULT = re.compile(r'<param name="defaultcontrol">\s*(\d+)\s*</param>|<defaultcontrol[^>]*>\s*(\d+)\s*</defaultcontrol>')
DEFAULT_PLACEHOLDER = 'DEXHUB_DEFAULT_CONTROL'


def _with_title(text, mark, parts):
    if text.count('<controls>') != 1 or text.count('</controls>') != 1:
        raise PatchError("the skin's information dialog is not as expected")
    start = text.index('<controls>')
    end = text.index('</controls>')
    if end < start or not parts.get('title_onload') or not parts.get('title_controls'):
        raise PatchError("the skin's information dialog is not as expected")
    body = text[start + len('<controls>'):end]
    # the dialog's default control: Dex Hub's group holds one with its id
    # (Kodi focuses it as the dialog opens, and the skin's own, hidden for a
    # Dex Hub title, would hand the focus on to the skin's hidden buttons)
    found = _DEFAULT.search(text[:start])
    default = (found.group(1) or found.group(2)) if found else '0'
    controls = parts['title_controls'].replace(DEFAULT_PLACEHOLDER, default)
    return ''.join([
        text[:start],
        _chunk("%s\n    <!-- Dex Hub: its title page for its own titles, the skin's page for every other -->\n"
               "%s    " % (mark, parts['title_onload'])),
        '<controls>',
        _chunk('\n        <control type="group">\n            <visible>%s</visible>' % OTHER),
        body,
        _chunk('        </control>\n%s    ' % controls),
        text[end:],
    ])


_EDITS = ((INCLUDES, 'include', _with_include), (INFO, 'title', _with_title))
# files an earlier version of the pages changed (taken out again by revert)
_OLD = (SEEKBAR,)


def _plan(root):
    """({rel: (old, new)}, {rel: source path} to copy in, [skipped part notes]).
    PatchError when the pages cannot go in at all."""
    data = pack()
    mark = marker()
    if not data or not mark:
        raise PatchError('the add-on has no pages for the skin')
    texts, skipped = {}, []
    for rel, part, edit in _EDITS:
        path = os.path.join(root, *rel.split('/'))
        try:
            old = _read(path)
        except Exception:
            if part == 'include':
                raise PatchError('%s is missing' % rel)
            skipped.append('%s: missing' % part)
            continue
        if MARKER_PREFIX in old or BEGIN in old:
            raise PatchError('%s has the pages already' % rel)
        try:
            new = edit(old, mark, data['parts']) if part != 'include' else edit(old, mark)
            _check(old, new, rel)
            if unpatch_text(new) != old:
                raise PatchError('%s: the pages would not come out of it exactly' % rel)
        except PatchError as exc:
            if part == 'include':
                raise
            skipped.append('%s: %s' % (part, exc))
            continue
        texts[rel] = (old, new)
    copies = {}
    folder = os.path.join(root, '1080i')
    window = fold_window()
    try:
        names = os.listdir(folder)
    except Exception:
        names = []
    for rel in data.get('copy') or []:
        name = rel.split('/')[-1]
        dest = os.path.join(root, *rel.split('/'))
        if os.path.exists(dest) and MARKER_PREFIX not in _read(dest):
            raise PatchError('the skin has a %s of its own' % name)
        if name.startswith('Custom_'):
            # Kodi takes one window per id: the skin's own would win
            taken = [n for n in names if n.lower().startswith('custom_%d_' % window) and n != name]
            if taken:
                raise PatchError('the skin has a window %d of its own (%s)' % (window, taken[0]))
        copies[rel] = os.path.join(data['root'], *rel.split('/'))
    return texts, copies, skipped


def status():
    """(state, note) of the skin in use: 'in' (this version of the pages is
    in), 'old' (an older one), 'ready' (they can go in), 'no' (this skin's
    text differs, or the add-on has no pages)."""
    try:
        root = _skin_root()
        text = _read(os.path.join(root, *INCLUDES.split('/')))
    except Exception:
        return 'no', 'Includes.xml'
    mark = marker()
    if not mark:
        return 'no', 'no pages in the add-on'
    if mark in text:
        missing = [rel for rel in pack().get('copy') or [] if not os.path.exists(os.path.join(root, *rel.split('/')))]
        return ('in', '') if not missing else ('old', 'missing %s' % missing)
    if MARKER_PREFIX in text:
        return 'old', ''
    try:
        _plan(root)
    except PatchError as exc:
        return 'no', str(exc)
    return 'ready', ''


def apply(backup_dir, record):
    """Put the pages into the skin in use (the originals of the files it
    changes are copied into <backup_dir>/skin/ first). ``record`` (the
    state's 'pages' dict) gets each file's sha1. Returns the parts skipped."""
    import shutil
    root = _skin_root()
    texts, copies, skipped = _plan(root)
    files = record.setdefault('files', {})
    added = record.setdefault('added', {})
    for rel, source in copies.items():
        dest = os.path.join(root, *rel.split('/'))
        tmp = dest + '.dexhub.tmp'
        shutil.copyfile(source, tmp)
        os.replace(tmp, dest)
        added[rel] = file_sha1(dest)
    for rel, (old, new) in texts.items():
        keep = os.path.join(backup_dir, 'skin', *rel.split('/'))
        os.makedirs(os.path.dirname(keep), exist_ok=True)
        if not os.path.exists(keep) or file_sha1(keep) != sha1(old):
            with open(keep, 'w', encoding='utf-8') as handle:
                handle.write(old)
        _write(os.path.join(root, *rel.split('/')), new)
        files[rel] = {'orig': sha1(old), 'patched': sha1(new)}
    record['version'] = version()
    record['skin_version'] = skin_version()
    record['skipped'] = skipped
    C.log('Arctic Fuse 3: Dex Hub pages %d in (%s)%s' % (
        version(), ', '.join(sorted(list(texts) + list(copies))),
        ('; skipped: %s' % '; '.join(skipped)) if skipped else ''))
    return skipped


def revert(backup_dir, record):
    """The pages out of the skin in use: their pieces out of the skin's
    files (each file exactly as it was; the backup when a file's pieces
    cannot be told apart, and only when it is the very file the pages
    wrote), the copied files out. Any release of the skin, whatever the
    record says (a skin updated since brought its own files back: nothing
    to take out of those)."""
    import shutil
    root = _skin_root()
    done = []
    files = record.get('files') or {}
    for rel in sorted(set(list(files) + [edit[0] for edit in _EDITS] + list(_OLD))):
        path = os.path.join(root, *rel.split('/'))
        try:
            text = _read(path)
        except Exception:
            continue
        if BEGIN not in text and MARKER_PREFIX not in text:
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
            C.log('Arctic Fuse 3: %s keeps pieces of the pages it cannot take out' % rel, xbmc.LOGWARNING)
    for rel in sorted(set(list((record.get('added') or {}).keys()) + list(pack().get('copy') or []))):
        path = os.path.join(root, *rel.split('/'))
        try:
            if os.path.exists(path) and MARKER_PREFIX in _read(path):
                os.remove(path)
                done.append(rel)
        except Exception as exc:
            C.log('Arctic Fuse 3: %s not removed: %s' % (rel, exc), xbmc.LOGWARNING)
    record.clear()
    if done:
        C.log('Arctic Fuse 3: Dex Hub pages taken out of %s' % ', '.join(sorted(done)))
    return done


# --------------------------------------------------------------------------
# what the add-on asks while it runs
# --------------------------------------------------------------------------

def installed():
    """The pages are chosen for the Arctic Fuse 3 in use (af3.json)."""
    if C.served() != 'af3':
        return False
    return bool((C.af3_state().get('pages') or {}).get('added'))


def fold_ready():
    """Kodi has the folder page (the skin was reloaded with it in)."""
    if not installed():
        return False
    try:
        import xbmcgui
        xbmcgui.Window(10000 + fold_window())
        return True
    except Exception:
        return False


_TITLE = {'t': 0.0, 'ok': False}


def title_ready():
    """The skin's information dialog has Dex Hub's title page (read again
    every few seconds: a skin update brings the skin's own back)."""
    import time
    if not installed():
        return False
    now = time.time()
    if now - _TITLE['t'] > 5.0:
        try:
            _TITLE['ok'] = marker() in _read(os.path.join(_skin_root(), *INFO.split('/')))
        except Exception:
            _TITLE['ok'] = False
        _TITLE['t'] = now
    return _TITLE['ok']


def strings():
    """{id: (english, arabic)} of the pages' labels."""
    data = C.read_json(os.path.join(pack_dir(), 'strings.json'), {}) or {}
    out = {}
    for key, value in data.items():
        try:
            out[int(key)] = (value[0], value[1])
        except Exception:
            continue
    return out
