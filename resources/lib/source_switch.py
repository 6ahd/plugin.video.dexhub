"""Player-panel switching without the browsing router or a second invoker."""
import time

import xbmc
import xbmcgui


def run(api, params):
    from .skinui import osd_sources
    from .playback.session import load_session, save_session

    pick = str(params.get('pick') or '')
    if pick == 'none':
        return
    ctx = load_session() or {}
    if not isinstance(ctx, dict):
        ctx = {}
    player = xbmc.Player()
    if params.get('rescan'):
        if not player.isPlayingVideo() or osd_sources.searching():
            return
        osd_sources.set_state('searching')
        last = [0.0]

        def progress(done, total, count):
            now = time.monotonic()
            if now - last[0] >= 0.8:
                last[0] = now
                osd_sources.set_state('searching:%d/%d' % (done, total))

        try:
            found = api.osd_rescan(ctx, on_progress=progress) or []
        except Exception as exc:
            xbmc.log('[DexHub] panel search failed: %s' % exc, xbmc.LOGWARNING)
            found = []
        osd_sources.set_state('done:%d' % len(found))
        return

    key = str(params.get('stream_key') or '')
    if not key and not pick.isdigit():
        # Old keyboard shortcuts without a panel row retain their picker.
        from . import plugin
        return plugin.switch_source(pick=pick)

    listed, has_current = osd_sources.rows()
    scope = osd_sources._item_key(ctx.get('canonical_id'), ctx.get('season'), ctx.get('episode'))
    if params.get('item') and params['item'] != scope:
        api.notify(api.tr('انتهت بيانات التشغيل. أعد اختيار المصدر.'))
        return
    if key:
        # The URL owns its identity even if new scan results moved its row.
        selected = next((r for r in listed if str(r.get('k') or '') == key), None)
        if selected is None:
            api.notify(api.tr('انتهت بيانات التشغيل. أعد اختيار المصدر.'))
            return
    else:
        # Compatibility with an old skin still sending numeric picks.
        index = int(pick)
        if index >= len(listed):
            return
        selected = listed[index]
        key = str(selected.get('k') or '')
    if selected.get('current') or key == str(ctx.get('stream_key') or ''):
        api.notify(api.tr('هذا المصدر يشتغل حالياً'))
        return
    if not key or not api.cache_store.get('stream', key):
        api.notify(api.tr('انتهت بيانات التشغيل. أعد اختيار المصدر.'))
        return

    started = time.monotonic()
    pos, duration = 0.0, 0.0
    try:
        if player.isPlaying():
            pos = float(player.getTime() or 0.0)
            duration = float(player.getTotalTime() or 0.0)
    except Exception:
        pass
    percent = max(0.0, min(100.0, pos / duration * 100.0)) if duration > 1 else 0.0
    subtitle = api._capture_external_subtitle_for_switch(player) or {}
    if pos > 1.0:
        snapshot = dict(ctx, resume_seconds=pos, source_switch_pending=True,
                        source_switch_captured_at=int(time.time()))
        if duration > 1.0:
            snapshot.update(duration=duration, resume_percent=percent)
        snapshot.update(subtitle)
        save_session(snapshot)
    xbmc.executebuiltin('Dialog.Close(1149,true)')
    xbmc.executebuiltin('Dialog.Close(videoosd,true)')
    xbmcgui.Window(10000).setProperty('dexhub.switch.inplace', str(int(time.time())))
    api._monitor_playback('source-switch-in-place', title=ctx.get('title') or '',
                          playback_uid=ctx.get('playback_uid') or '')
    xbmc.log('[DexHub timing] stage=source-switch-prepare ms=%.1f' %
             ((time.monotonic() - started) * 1000), xbmc.LOGINFO)
    # Keep the existing playback engine, subtitle restoration, StartOffset and
    # Kodi 22 safe playlist handoff; no stop/reset of the hardware decoder.
    return api.play(key, resume_seconds=('%.3f' % pos if pos > 1.0 else ''),
                    resume_percent=('%.2f' % percent if 1.0 < percent < 95.0 else ''),
                    switch_subtitle_path=subtitle.get('switch_subtitle_path', ''),
                    switch_subtitle_enabled=('0' if subtitle.get('switch_subtitle_enabled') is False else '1'),
                    switch_subtitle_name=subtitle.get('switch_subtitle_name', ''))
