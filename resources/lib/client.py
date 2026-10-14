# -*- coding: utf-8 -*-
"""Backward-compat alias for dexhub.client.

v5.10.77: this used to be `from dexhub.client import *`. That absolute import only
resolves because default.py puts resources/lib on sys.path, and it loads the
file a SECOND time under the top-level name `dexhub.client`, beside the
`resources.lib.dexhub.client` everything else imports. Two module objects meant
two HTTP connection pools that never shared a TLS connection, two lane-pool
sets, two in-memory caches, and two different locks guarding the same files.

Replacing this module in sys.modules makes `resources.lib.client` the very same
object as `resources.lib.dexhub.client`: one pool, one cache, one lock, and every
name — including reassigned globals and private helpers — reads through.
"""
import sys as _sys

from .dexhub import client as _real

_sys.modules[__name__] = _real
