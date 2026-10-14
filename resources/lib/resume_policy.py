# -*- coding: utf-8 -*-
"""One resume point per title: the most recent one wins.

v5.10.84. A server's position and Dex Hub's own record can disagree (kodi.log,
Mutiny: Dex Hub had 306s, Plex 1512s). Each side carries the time it was last
written, so the newer one is used: Plex lastViewedAt, Emby LastPlayedDate,
and updated_at for Dex Hub's local record. A local point under 30s never
overrides a server, and ties go to the server.
"""


def newest_seconds(item, backend='plex'):
    server_seconds = float(item.get('view_offset_ms') or 0) / 1000.0
    try:
        from .native_continue import _epoch
        from . import playback_store
        server_at = _epoch(item.get('last_viewed_at') or item.get('last_played_date'))
        server_id = str(item.get('server_id') or '')
        item_id = str(item.get('rating_key') or '')
        imdb = str((item.get('ids') or {}).get('imdb_id') or '')
        is_movie = str(item.get('media_type') or '') == 'movie'
        best = None
        for row in playback_store.list_continue_items(limit=500) or []:
            same = ((item_id and str(row.get('native_item_id') or '') == item_id
                     and str(row.get('native_server_id') or '') == server_id)
                    or (is_movie and imdb and str(row.get('canonical_id') or '') == imdb))
            if same and (best is None or _epoch(row.get('updated_at')) > _epoch(best.get('updated_at'))):
                best = row
        if best is not None:
            local_seconds = float(best.get('position') or 0)
            local_at = _epoch(best.get('updated_at'))
            if local_seconds >= 30.0 and local_at > server_at + 5.0:
                try:
                    import xbmc
                    xbmc.log('[DexHub] resume: Dex Hub point %.0fs is newer than %s %.0fs; using it' % (
                        local_seconds, backend, server_seconds), xbmc.LOGINFO)
                except Exception:
                    pass
                return local_seconds
    except Exception:
        pass
    return server_seconds
