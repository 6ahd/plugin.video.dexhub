# -*- coding: utf-8 -*-
"""v5.10.586: Kodi tells a script to stop through the xbmc.Monitor objects that exist when it
stops it (CPythonInvoker::stop -> AbortNotification, once): a Monitor made afterwards never
hears it, its abortRequested() stays False and its waitForAbort() runs to its timeout.

Dex Hub's service makes its Monitors as each part of it starts. Closing Kodi a few seconds
after it started (CoreELEC box, 18 Nov 2026) stopped the service before most of them existed:
the Home rows, the sync, the file compiler went on, Kodi killed the service after 5 s ("script
didn't stop in 5 seconds"), the rest ran on after the skin was unloaded, and Kodi hung until
the box restarted.

install() makes one Monitor first thing in the service and puts a Monitor class in xbmc's
place whose instances also answer for that first one. A Monitor that existed at the stop is
told by Kodi; one made after it learns from the first one at once. Only the service's own
interpreter is changed."""
import xbmc

_base = None
_first = None


def install():
    """Never stops the service: on any error xbmc.Monitor stays Kodi's own."""
    global _base, _first
    if _first is not None:
        return
    try:
        base = xbmc.Monitor
        first = base()

        class Monitor(base):
            def abortRequested(self):
                return base.abortRequested(first) or base.abortRequested(self)

            def waitForAbort(self, timeout=None):
                if base.abortRequested(first):
                    return True
                if timeout is None:
                    return base.waitForAbort(self)
                return base.waitForAbort(self, timeout)

        # (no renaming: Kodi's Monitor type refuses a new __name__, 18 Nov 2026)
        _base, _first = base, first
        xbmc.Monitor = Monitor
    except Exception as exc:
        try:
            xbmc.log('[DexHub] abort guard not installed: %s' % exc, xbmc.LOGWARNING)
        except Exception:
            pass


def aborting():
    """True once Kodi has asked the service to stop."""
    return _first is not None and _base.abortRequested(_first)
