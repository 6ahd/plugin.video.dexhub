"""Close only already-loaded network pools, including the legacy import alias."""
def shutdown_loaded_pools():
    import sys
    seen = set()
    for name in ('dexhub.client', __package__ + '.dexhub.client', __package__ + '.client'):
        module = sys.modules.get(name)
        callback = getattr(module, 'shutdown_lane_pools', None) if module is not None else None
        if callback is None or id(callback) in seen:
            continue
        seen.add(id(callback))
        try:
            callback()
        except Exception:
            pass


def release_retired_pools():
    """v5.10.448: free the closed lane executors (kept until their workers
    ended, dexhub/client.py RETIRED), under every name the module loaded."""
    import sys
    seen = set()
    for name in ('dexhub.client', __package__ + '.dexhub.client', __package__ + '.client'):
        module = sys.modules.get(name)
        callback = getattr(module, 'release_retired', None) if module is not None else None
        if callback is None or id(callback) in seen:
            continue
        seen.add(id(callback))
        try:
            callback()
        except Exception:
            pass
