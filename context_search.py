# -*- coding: utf-8 -*-
import os
import sys

_lib = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'lib')
if _lib not in sys.path:
    sys.path.insert(0, _lib)

from resources.lib.context_play import run

if __name__ == '__main__':
    run('search')
