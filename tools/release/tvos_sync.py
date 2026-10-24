#!/usr/bin/env python3
"""Bring the Apple TV add-on (plugin.video.dexhub.tvos) up to this repository.

    python3 tools/release/tvos_sync.py PREVIOUS_TVOS.zip BASE_REF OUT_DIR [--head HEAD] [--version X]

PREVIOUS_TVOS.zip is the last Apple TV release, BASE_REF the commit of the
regular add-on it was made from (e.g. the 5.10.401 commit). Every file that
changed from BASE_REF to --head is merged three ways into the Apple copy:
its own changes (the .tvos ids, Aether and the external players, the files
only it has) stay, and the regular add-on's fixes come in. Files the update
does not touch are left byte for byte. addon.xml keeps the Apple id and name
and takes the new version and <news>.

The Apple build fell behind the regular one this way before (5.10.401 while
the regular was 5.10.404); one run per release keeps them together.
Writes OUT_DIR/plugin.video.dexhub.tvos and OUT_DIR/plugin.video.dexhub.tvos-<version>.zip.
"""
import argparse
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

TVOS = 'plugin.video.dexhub.tvos'
NOT_SHIPPED = re.compile(r'^(tools/|\.github/|README\.md$|\.gitignore$|\.gitattributes$|LICENSE$|[^/]+\.jpg$)')


def apple_names(text):
    """The regular ids as the Apple build writes them (a docstring's
    'plugin.video.dexhub.' and other add-ons' names are left alone)."""
    text = re.sub(r'plugin\.video\.dexhub(?![.\w])', TVOS, text)
    # a line naming both skins (('skin.dexhub', 'skin.dexhub.tvos')) is kept
    text = ''.join(line if 'skin.dexhub.tvos' in line else re.sub(r'skin\.dexhub(?![.\w])', 'skin.dexhub.tvos', line)
                   for line in text.splitlines(True))
    # the windows read the Apple add-on's own asset root (skin 3.28.115)
    return text.replace('dexhub.assetroot', 'dexhub.tvos.assetroot')


def git(repo, *args, binary=False):
    out = subprocess.run(['git', '-C', repo] + list(args), check=True, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL if args[0] == 'show' else None).stdout
    return out if binary else out.decode('utf-8')


def show(repo, ref, path):
    try:
        return git(repo, 'show', '%s:%s' % (ref, path), binary=True)
    except subprocess.CalledProcessError:
        return None


def _explode(text):
    # window XMLs merge per tag, not per (very long) line; undone exactly after
    return text.replace('><', '>\n\x1e<')


def _implode(text):
    return text.replace('>\n\x1e<', '><')


def merge(cur, base, other, path):
    """3-way merge of text; None on a conflict."""
    one_line = path.endswith('.xml')     # window XMLs keep whole cards on one line
    parts = [_explode(x) if one_line else x for x in (cur, base, other)]
    with tempfile.TemporaryDirectory() as tmp:
        names = []
        for name, text in zip(('cur', 'base', 'other'), parts):
            names.append(os.path.join(tmp, name))
            with open(names[-1], 'w', encoding='utf-8', newline='') as fh:
                fh.write(text)
        rc = subprocess.run(['git', 'merge-file', '-q'] + names).returncode
        if rc != 0:
            return None
        result = open(names[0], encoding='utf-8', newline='').read()
    return _implode(result) if one_line else result


def addon_xml(cur, head_xml, version):
    text = re.sub(r'(<addon\b[^>]*\bversion=")[^"]+(")', r'\g<1>%s\2' % version, cur, count=1)
    # the entry points' libraries follow the regular add-on (5.10.408: the
    # screensaver's entry is default.py)
    for point in re.findall(r'<extension point="xbmc\.ui\.screensaver"[^>]*/>', head_xml):
        text = re.sub(r'<extension point="xbmc\.ui\.screensaver"[^>]*/>', lambda m: point, text, count=1)
    news = re.search(r'<news>.*?</news>', head_xml, re.S)
    if news:
        text = re.sub(r'<news>.*?</news>', lambda m: news.group(0), text, count=1, flags=re.S)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('previous_zip')
    ap.add_argument('base_ref')
    ap.add_argument('out')
    ap.add_argument('--head', default='HEAD')
    ap.add_argument('--version')
    args = ap.parse_args(argv)
    repo = git(os.path.dirname(os.path.abspath(__file__)), 'rev-parse', '--show-toplevel').strip()

    out = os.path.abspath(args.out)
    dest = os.path.join(out, TVOS)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(out, exist_ok=True)
    with zipfile.ZipFile(args.previous_zip) as zf:
        zf.extractall(out)
    if not os.path.isdir(dest):
        sys.exit('%s holds no %s folder' % (args.previous_zip, TVOS))

    head_xml = show(repo, args.head, 'addon.xml').decode('utf-8')
    version = args.version or re.search(r'<addon\b[^>]*\bversion="([^"]+)"', head_xml).group(1)
    changed = [p for p in git(repo, 'diff', '--name-only', args.base_ref, args.head).splitlines()
               if p and not NOT_SHIPPED.match(p)]
    conflicts, done = [], 0
    for path in changed:
        target = os.path.join(dest, path)
        base, other = show(repo, args.base_ref, path), show(repo, args.head, path)
        if path == 'addon.xml':
            cur = open(target, encoding='utf-8').read()
            new = addon_xml(cur, head_xml, version)
        elif other is None:                     # gone from the regular add-on
            if os.path.exists(target):
                os.remove(target)
            done += 1
            continue
        else:
            try:
                other_text = other.decode('utf-8')
            except UnicodeDecodeError:          # an image or other binary: the new one
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, 'wb') as fh:
                    fh.write(other)
                done += 1
                continue
            other_text = apple_names(other_text)
            if not os.path.exists(target) or base is None:
                new = other_text                # a new file
            else:
                cur = open(target, encoding='utf-8', newline='').read()
                new = merge(cur, apple_names(base.decode('utf-8')), other_text, path)
                if new is None:
                    conflicts.append(path)
                    continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w', encoding='utf-8', newline='') as fh:
            fh.write(new)
        done += 1
    with tempfile.TemporaryDirectory() as tmp:     # every module still compiles
        for root, _dirs, files in os.walk(dest):
            for name in files:
                if name.endswith('.py'):
                    py_compile.compile(os.path.join(root, name), cfile=os.path.join(tmp, 'x.pyc'), doraise=True)
    print('%d file(s) brought over, version %s' % (done, version))
    if conflicts:
        print('CONFLICT, merge these by hand (the Apple copy is unchanged):\n  ' + '\n  '.join(conflicts))
        return 1
    zpath = os.path.join(out, '%s-%s.zip' % (TVOS, version))
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(dest):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, out))
    print('ready: %s' % zpath)
    return 0


if __name__ == '__main__':
    sys.exit(main())
