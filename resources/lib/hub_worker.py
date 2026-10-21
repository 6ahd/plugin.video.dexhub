# -*- coding: utf-8 -*-
"""v5.10.366: Dex Hub Extra in the service: a thread that sleeps.

Not linked: it only looks at one Home property every few seconds (set when
the TV is linked). Linked: once at start it asks whether Extra is on and
brings the cloud look; then a marked look change is sent when it settled,
and while the TV is in a watch party the party's loop runs here (it has to:
Kodi tells a player in the service when the video pauses or seeks).
"""
import xbmc
import xbmcgui


def run(monitor):
    if monitor.waitForAbort(15):
        return
    from . import hub_client, cloud_look, watch_party
    home = xbmcgui.Window(10000)
    started = False
    wake = None
    import time
    accounts_at = 0.0
    while not monitor.abortRequested():
        try:
            now_wake = home.getProperty('dexhub.hub.wake')
            if now_wake != wake:
                wake = now_wake
                if wake:
                    started = False          # linked again, or unlinked: look again
            if hub_client.linked():
                try:
                    from . import remote_setup       # v5.10.374: the setup page from the account's site
                    remote_setup.ensure(monitor)
                except Exception as exc:
                    xbmc.log('[DexHub] remote setup: %s' % exc, xbmc.LOGINFO)
                if not started:
                    started = True
                    hub_client.extra(force=True)
                    cloud_look.at_start()
                    hub_client.pull_accounts()         # v5.10.370
                    accounts_at = time.time()
                elif time.time() - accounts_at > 15 * 60 and not xbmc.Player().isPlayingVideo():
                    accounts_at = time.time()
                    hub_client.pull_accounts()         # one short answer when nothing changed
                cloud_look.tick()
                try:
                    from . import cloud_profile      # v5.10.371: the whole setup, every six hours when changed
                    cloud_profile.tick()
                except Exception as exc:
                    xbmc.log('[DexHub] whole setup: %s' % exc, xbmc.LOGINFO)
                if not watch_party.room():
                    watch_party.resume()             # v5.10.375: back in a kept party
                if watch_party.room():
                    watch_party.loop(monitor)
        except Exception as exc:
            xbmc.log('[DexHub] hub thread: %s' % exc, xbmc.LOGWARNING)
        if monitor.waitForAbort(5):
            break
