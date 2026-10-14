"""Per-invoker UI callback binding; never imports a router on demand."""
_runtime = None


def bind(runtime):
    global _runtime
    _runtime = runtime


def current(required=True):
    if _runtime is None and required:
        raise RuntimeError('Player/UI runtime was not bound for this invocation')
    return _runtime
