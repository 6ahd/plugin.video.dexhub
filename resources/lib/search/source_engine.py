"""Shared source coordinator. Imports no Kodi, UI or catalog modules.
Adapters are supplied by the caller; legacy implementations remain available.
"""

# v5.10.85: longest a warm server's scan may run before it stops on its own.
_NATIVE_SCAN_CAP = 20.0


def _cut_reason(server, payload):
    """'deadline', 'cancelled' or '' for a native lookup that just returned.

    v5.10.91: set by the stop signal (dexhub.client.install_stop_signal) when
    the scan code saw it fire, or by _native_payload_budgeted when the budget
    ran out before the lookup thread came back.
    """
    try:
        status = payload.get('_source_status') if isinstance(payload, dict) else ''
        if status in ('deadline', 'cancelled'):
            return status
        reason = (server or {}).get('_stop_reason') or ''
        return reason if reason in ('deadline', 'cancelled') else ''
    except Exception:
        return ''


def _fetch_uncached(target, media_type, api):
    """Fetch one source payload and feed the local adaptive scheduler.

    The scheduler stores only latency/success counters; no media ids, URLs,
    credentials or viewing data are persisted.
    """
    provider, request_id = target
    started = api.time.monotonic()
    payload = {}
    failed = False
    try:
        if (provider or {}).get('id') in ('__plex__', '__emby__', '__silo__', '__jellyfin__'):
            backend = provider['id'].strip('_')
            server = (provider or {}).get('_server') or {}
            # v5.10.91: the budget is decided ONCE per lookup (a slow server's
            # periodic full-length probe is consumed by asking).
            _budget, _why = 0.0, ''
            try:
                if hasattr(api._server_health, 'standing'):
                    _budget, _why, _streak = api._server_health.standing(backend, server)
                else:
                    _budget = float(api._server_health.lookup_budget(backend, server) or 0.0)
                    _why = 'empty' if _budget > 0 else ''
            except Exception:
                _budget, _why = 0.0, ''
            try:
                # v5.10.85: the lookup may outlive the budget in its own
                # thread; hand it a stop signal it can poll (see client.py).
                from ..dexhub import client as _client
                _client.install_stop_signal(server, _budget if _budget > 0 else _NATIVE_SCAN_CAP)
            except Exception:
                pass
            if not provider.get('_force_stream_refresh') and (not api._server_health.should_query(backend, server)):
                api.xbmc.log('[DexHub] %s server temporarily skipped by health router: %s' % (backend, server.get('name') or '?'), api.xbmc.LOGINFO)
                return {'streams': [], '_source_status': 'skipped'}
            native_started = api.time.monotonic()
            try:
                if _budget > 0:
                    payload = api._native_payload_budgeted(provider, media_type, backend, _budget)
                else:
                    payload = api._plex_stream_payload(provider, media_type) if backend == 'plex' else api._emby_stream_payload(provider, media_type)
                native_count = len((payload or {}).get('streams') or [])
                _elapsed = api.time.monotonic() - native_started
                _cut = _cut_reason(server, payload) if native_count == 0 else ''
                _note = ''
                if _cut == 'cancelled':
                    # The user picked a source first: nothing learned about
                    # this server, so nothing is recorded.
                    _note = ' (stopped: search cancelled, not counted)'
                elif _cut == 'deadline':
                    # v5.10.91: out of time is SLOW, never EMPTY.
                    _record_cut = getattr(api._server_health, 'record_cut', None)
                    if _record_cut is not None:
                        _record_cut(backend, server, _elapsed)
                        _slow = api._server_health.slow_streak(backend, server)
                    else:
                        _slow = 0
                    _note = ' (stopped at the %.0fs limit, not counted as empty; %d slow in a row)' % (
                        _budget if _budget > 0 else _NATIVE_SCAN_CAP, _slow)
                else:
                    api._server_health.record_result(backend, server, _elapsed, True, native_count)
                if _why == 'empty':
                    _note += ' (cold: %.0fs budget, %d empty scans)' % (_budget, api._server_health.empty_streak(backend, server))
                elif _why == 'slow':
                    _note += ' (slow server: %.0fs budget)' % _budget
                elif _why == 'probe':
                    _note += ' (slow server: periodic full-length try)'
                api.xbmc.log('[DexHub] %s Native source result: server=%s streams=%d%s' % (backend.capitalize(), server.get('name') or '?', native_count, _note), api.xbmc.LOGINFO)
                return payload
            except Exception:
                api._server_health.record_result(backend, server, api.time.monotonic() - native_started, False, 0)
                raise
        _request_ids = list((provider or {}).get('_candidate_request_ids') or [request_id])
        _timeout = api._stream_timeout_for_provider(provider)
        _last_exc = None
        try:
            _one = api._stremio_provider.fetch(provider, media_type, _request_ids[0], timeout_override=_timeout)
            _rows = (_one or {}).get('streams') or [] if isinstance(_one, dict) else []
            if isinstance(_one, dict) and _one.get('_source_status') == 'skipped':
                payload = _one
                return payload
            if isinstance(_rows, dict):
                _rows = [_rows]
            if _rows:
                try:
                    provider['_resolved_request_id'] = str(_request_ids[0])
                except Exception:
                    pass
                payload = _one
                return payload
        except Exception as exc:
            _last_exc = exc
        if len(_request_ids) > 1:
            _alt, _alt_id, _alt_exc = api._fetch_alternate_ids_parallel(provider, media_type, _request_ids[1:], _timeout)
            if _alt is not None:
                try:
                    provider['_resolved_request_id'] = str(_alt_id)
                except Exception:
                    pass
                payload = _alt
                return payload
            _last_exc = _alt_exc or _last_exc
        if _last_exc is not None:
            failed = True
            return _last_exc
        payload = {'streams': []}
        return payload
    except Exception as exc:
        failed = True
        api.xbmc.log('[DexHub] provider source error: %s' % exc, api.xbmc.LOGWARNING)
        return exc
    finally:
        try:
            count = len((payload or {}).get('streams') or []) if isinstance(payload, dict) else 0
            api._provider_stats.record(provider, api.time.monotonic() - started, success=not failed, result_count=count)
        except Exception:
            pass


def fetch(target, media_type, api):
    """Cancellation and a short per-title results cache around the real fetch.

    v5.10.84: once the user has picked a source the search is cancelled, and a
    provider task that has not started yet returns at once instead of querying.
    A provider that answered for the same title minutes ago answers again from
    the results cache. _force_stream_refresh bypasses the cache.
    """
    provider, request_id = target
    try:
        from ..dexhub import client as _client
        if _client.task_cancelled():
            return {'streams': [], '_source_status': 'cancelled'}
    except Exception:
        pass
    cache_key = ''
    try:
        from . import results_cache as _results
        cache_key = _results.key(provider, media_type, request_id)
        if cache_key and not (provider or {}).get('_force_stream_refresh'):
            entry = _results.get(cache_key)
            if entry is not None:
                if entry.get('resolved_request_id'):
                    try:
                        provider['_resolved_request_id'] = entry['resolved_request_id']
                    except Exception:
                        pass
                payload = entry.get('payload') or {}
                api.xbmc.log('[DexHub] source cache hit: %s (%d streams)' % (
                    (provider or {}).get('name') or (provider or {}).get('id') or '?',
                    len(payload.get('streams') or [])), api.xbmc.LOGINFO)
                return payload
    except Exception:
        cache_key = ''
    payload = _fetch_uncached(target, media_type, api)
    if cache_key:
        try:
            from . import results_cache as _results
            _results.put(cache_key, payload, (provider or {}).get('_resolved_request_id') or '')
        except Exception:
            pass
    return payload
