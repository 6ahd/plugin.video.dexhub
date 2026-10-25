#!/usr/bin/env python3
"""The Home's first rows load together (add-on 5.10.424, skin 3.28.139).

    python3 tools/release/skin_rows_together.py SKIN_DIR [--check]

skin.dexhub gives a Home row its path (DhsRowPath<n> in Includes_DexHub.xml)
only once the row above has its first title, and row 1 once the spotlight
has its own: one plugin call at a time, so the first screen filled row after
row. Each call is a few milliseconds of Dex Hub's row cache plus a Python
start, so the first TOGETHER rows load at once instead: their "the one above
is in" wait goes. What stays: the reach (rows 3 and down load as the cursor
nears them, dhs.rreach), the hold (a row keeps its path while its list holds
it) and the staging below row TOGETHER.

Each variable's value is (tab and the wait) | (tab and the hold), with the
reach beside the wait from row 3; the wait is taken out only when it is
exactly that (it names the row above's empty mark and its list, or the
spotlight's), else the file is left as it was and the row is named. A second
run changes nothing.
"""
import argparse
import os
import re
import sys

TOGETHER = 4
TABS = ('all', 'movie', 'series', 'servers', 'library')


def _tab(tab):
    if tab == 'all':
        return ('[String.IsEmpty(Window(Home).Property(dhs.tab)) | '
                'String.IsEqual(Window(Home).Property(dhs.tab),all)]')
    return 'String.IsEqual(Window(Home).Property(dhs.tab),%s)' % tab


def _close(text, start):
    """The index after the bracket that closes the one at start."""
    depth = 0
    for i in range(start, len(text)):
        depth += {'[': 1, ']': -1}.get(text[i], 0)
        if depth == 0:
            return i + 1
    raise ValueError('unbalanced')


def _is_wait(part, tab, n):
    if 'load.window' in part or 'rreach' in part:
        return False
    if n == 1:
        return 'dhs.%s.spot.' % tab in part and 'Container(4000)' in part
    return ('dhs.%s.%d.empty' % (tab, n - 1) in part and 'Container(%d)' % (5000 + n - 1) in part)


def unwait(text):
    """text with the first rows' waits taken out; (new text, changed, refused)."""
    changed, refused = [], []
    for n in range(1, TOGETHER + 1):
        head = '<variable name="DhsRowPath%d">' % n
        i = text.find(head)
        if i < 0:
            refused.append('DhsRowPath%d missing' % n)
            continue
        j = text.index('</variable>', i)
        body = text[i:j]
        for tab in TABS:
            path = '$INFO[Window(Home).Property(dhs.%s.%d.path)]' % (tab, n)
            m = re.search(r'<value condition="([^"]*)">%s</value>' % re.escape(path), body)
            if not m:
                refused.append('DhsRowPath%d %s: no value' % (n, tab))
                continue
            cond = m.group(1)
            at = cond.find(_tab(tab))
            if at < 0:
                refused.append('DhsRowPath%d %s: no tab condition' % (n, tab))
                continue
            after = at + len(_tab(tab))
            if not cond.startswith(' + [', after):
                continue                        # already together
            end = _close(cond, after + 3)
            if not _is_wait(cond[after + 3:end], tab, n):
                refused.append('DhsRowPath%d %s: not the wait it should be' % (n, tab))
                continue
            new_cond = cond[:after] + cond[end:]
            body = body[:m.start(1)] + new_cond + body[m.end(1):]
            changed.append('%d:%s' % (n, tab))
        text = text[:i] + body + text[j:]
    return text, changed, refused


def run(skin, check=False):
    path = os.path.join(skin, 'xml', 'Includes_DexHub.xml')
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    new, changed, refused = unwait(text)
    if refused:
        print('left as it was: ' + '; '.join(refused))
        return []
    if changed and not check:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(new)
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
