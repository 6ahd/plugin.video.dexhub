# -*- coding: utf-8 -*-
"""v5.10.448: the title page comes back when something closed it under a
search. A source search started on a title page runs over it (its window and
the results are dialogs on top of the page), but any Kodi add-on Dex Hub asks
can close every dialog (Fen Light ended a search that found nothing with
Dialog.Close(all)), and Back from the results then landed on Home.

  note()       the search window opens: remembers the open page's title
  no_choice()  the search or its results closed without a pick
  restore()    the end of the call (default.py): the page opens again when
               it was open, the user picked nothing and it is gone; never
               over playback
The bridge stops the add-ons it can reach from closing dialogs; this is the
net under every other way.

  watch(win)   Dex Hub's search and results windows: Kodi's Dialog.Close(all)
               takes them off the screen but never returns their doModal, so
               the call hung, the page never came back and Kodi ignored every
               key ("topmost modal dialog closing animation is running"). A
               window gone for 1.5 s with nothing playing closes itself
               (the results window stays under the player while it plays).
  unwatch(win) the window closes itself"""
import xbmc

_NOTE = {}


def note():
    if not xbmc.getCondVisibility('Window.IsVisible(movieinformation)'):
        return
    query = xbmc.getInfoLabel('Window(Home).Property(dhs.tp.q)')
    if query:
        _NOTE.clear()
        _NOTE['q'] = query
        xbmc.log('[DexHub] page guard: the search runs over the title page', xbmc.LOGINFO)


def no_choice():
    if _NOTE.get('q') and not _NOTE.get('no_choice'):
        _NOTE['no_choice'] = True
        xbmc.log('[DexHub] page guard: closed with no pick', xbmc.LOGINFO)


def watch(window):
    import threading
    import xbmcgui
    try:
        wid = int(xbmcgui.getCurrentWindowDialogId())
    except Exception:
        return
    if wid < 13000 or getattr(window, '_dexhub_watch', None):
        return
    window._dexhub_watch = wid

    def run():
        monitor, gone = xbmc.Monitor(), 0
        while not monitor.abortRequested() and getattr(window, '_dexhub_watch', None) == wid:
            if xbmc.getCondVisibility('Window.IsVisible(%d) | Player.HasMedia' % wid):
                gone = 0
            else:
                gone += 1
                if gone >= 3:
                    window._dexhub_watch = None
                    xbmc.log('[DexHub] window %d was closed by something else: its call released' % wid, xbmc.LOGINFO)
                    no_choice()
                    try:
                        window.close()
                    except Exception:
                        pass
                    return
            monitor.waitForAbort(0.5)

    threading.Thread(target=run, name='DexHubDialogWatch', daemon=True).start()


def unwatch(window):
    try:
        window._dexhub_watch = None
    except Exception:
        pass


def restore():
    query, unpicked = _NOTE.pop('q', ''), _NOTE.pop('no_choice', False)
    if not query or not unpicked:
        return
    if xbmc.getCondVisibility('Window.IsVisible(movieinformation) | Player.HasMedia'):
        return
    xbmc.log('[DexHub] the title page was closed under its source search: opened again', xbmc.LOGINFO)
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.dexhub/?action=skin_open&%s)' % query)
