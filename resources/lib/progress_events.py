"""Explicit progress edits: invalidate cached rows and wake visible pages."""
import uuid

ACTION_REVISION = 'dhs.progress.action'


def revision():
    try:
        import xbmcgui
        return xbmcgui.Window(10000).getProperty(ACTION_REVISION) or ''
    except Exception:
        return ''


def changed():
    # A manual edit is urgent; routine player writes retain their debounce.
    try:
        from . import widget_cache
        widget_cache.mark_changed()
    except Exception:
        pass
    try:
        import xbmcgui
        import time
        window = xbmcgui.Window(10000)
        window.setProperty(ACTION_REVISION, uuid.uuid4().hex)
        window.setProperty('dhs.progress.changed', '%.6f' % time.time())
    except Exception:
        pass
