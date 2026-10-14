"""Explicit single-provider retry, never triggered by focus or selection."""
import threading
import xbmcgui

def fetch(state, meta, api):
    provider, request = state['target']
    provider = dict(provider, _force_stream_refresh=True)
    result = {}
    finished = threading.Event()
    def worker():
        try:
            result['data'] = api._fetch_stream_payload((provider, request), state['media_type'])
        except Exception as exc:
            result['data'] = exc
        finally:
            finished.set()
    threading.Thread(target=worker, name='DexHubSingleRetry', daemon=True).start()
    dialog = xbmcgui.DialogProgress()
    dialog.create('إعادة محاولة المصدر', state['name'])
    try:
        while not finished.wait(0.1):
            if dialog.iscanceled():
                return []
        if dialog.iscanceled():
            return []
    finally:
        dialog.close()
    data = result.get('data')
    if not isinstance(data, dict) or not data.get('streams'):
        xbmcgui.Dialog().notification('Dex Hub', 'لم تصل نتائج جديدة؛ النتائج السابقة محفوظة')
        return []
    rows = []
    canonical = meta.get('canonical_id') or request
    ids = api._merge_seed_ids(api.extract_ids(dict(meta, id=canonical)), api._get_tmdbh_seed_ids(canonical))
    api._append_stream_entries_from_data(rows, data, provider,
        provider.get('_resolved_request_id') or request, state['media_type'], canonical,
        meta, meta, meta.get('background') or meta.get('fanart') or '', ids, title=meta.get('title') or '',
        season=meta.get('season'), episode=meta.get('episode'),
        video_id=meta.get('video_id'), show_title=meta.get('show_title') or '',
        resume_seconds=meta.get('resume_seconds') or 0, resume_percent=meta.get('resume_percent') or 0)
    return rows
