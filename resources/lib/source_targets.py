"""Shared source_targets. No imports or side effects until called.

The caller supplies Kodi and compatibility adapters; Home/catalog code does not
belong here. Relative feature imports remain lazy and retain package semantics.
"""

def build_targets(api, media_type, canonical_id, meta=None, season=None, episode=None):
    targets = []
    native_targets = []
    try:
        for server in api.jellyfin_client.servers():
            native_targets.append(({
                'id': '__jellyfin__', 'name': 'Jellyfin • %s' % server.get('name', 'Jellyfin'),
                '_server': server, '_title': (meta or {}).get('name') or (meta or {}).get('title') or '',
                '_ids': api.extract_ids(dict(meta or {}, id=(meta or {}).get('id') or canonical_id)), '_season': season, '_episode': episode,
            }, str(canonical_id)))
    except Exception:
        pass
    seen = set()
    meta = api._meta_with_seed_ids(meta or {}, api._apply_anime_id_policy(api._enrich_stream_ids(api.extract_ids(meta or {}), media_type, meta=meta or {}, canonical_id=canonical_id, title=(meta or {}).get('name') or (meta or {}).get('title') or ''), media_type))
    candidate_ids = api._candidate_ids_from_meta(canonical_id, meta=meta, season=season, episode=episode, include_episode_variants=bool(season and episode))
    try:
        from .source_addons import skipped as _skipped, key as _addon_key
        _left_out = _skipped()
    except Exception:
        _left_out, _addon_key = frozenset(), None
    for provider in api.store.list_providers():
        if not api._get_bool_setting('stremio_sources_enabled', True):
            break
        if (provider or {}).get('enabled') is False:
            continue
        # v5.10.203: an add-on the user left out of the source search
        if _left_out and _addon_key and _addon_key(provider) in _left_out:
            continue
        if not api._stremio_provider.supports_stream(provider, media_type):
            continue
        prefixes = api._stream_resource_id_prefixes(provider, media_type=media_type)
        provider_ids = api._candidate_ids_matching_prefixes(candidate_ids, prefixes)
        if not provider_ids:
            # Stremio itself lets the addon decide if an id is valid. This keeps
            # dynamic aggregators such as AIOStreams visible even when their
            # manifest prefixes do not cover the current catalog id shape.
            provider_ids = list(candidate_ids or [])
        if season and episode:
            ep_suffix = ':%s:%s' % (int(season), int(episode))
            episode_ids = [x for x in provider_ids if str(x).endswith(ep_suffix)]
            plain_ids = [x for x in provider_ids if x not in episode_ids]
            provider_ids = episode_ids + plain_ids
        provider_ids = api._ordered_stream_candidate_ids(provider_ids, season=season, episode=episode)
        # v5.10.14: one race job per provider. Older builds launched up to
        # four ID variants for the SAME addon in parallel. On episodes this
        # could make Dexstreams return 149 rows for tt...:S:E and another 291
        # for the plain show id, wasting CPU while DexWorld Pro/Silo waited in
        # the queue. Keep the ordered fallbacks on the provider and try the
        # next ID only when the previous one returns zero playable streams.
        if provider_ids:
            _prov = dict(provider)
            _prov['_candidate_request_ids'] = list(provider_ids[:4])
            item_id = _prov['_candidate_request_ids'][0]
            key = (_prov.get('id') or _prov.get('base_url') or _prov.get('name') or '', '__provider__')
            if key not in seen:
                seen.add(key)
                targets.append((_prov, item_id))
    if not targets:
        for provider in api._provider_order_for_id(media_type, canonical_id):
            if _left_out and _addon_key and _addon_key(provider) in _left_out:
                continue
            key = (provider.get('id') or provider.get('base_url') or provider.get('name') or '', str(canonical_id))
            if key in seen:
                continue
            seen.add(key)
            targets.append((provider, canonical_id))
    # Connected Plex servers join the SAME parallel scan as a built-in
    try:
        if api.emby_client.is_signed_in() and api._get_bool_setting('emby_in_sources', True):
            _em_title = (meta or {}).get('name') or (meta or {}).get('title') or ''
            _em_ids = api.extract_ids(meta or {})
            for _esrv in api.emby_client.servers():
                native_targets.append(({
                    'id': '__emby__',
                    'name': 'Emby • %s' % (_esrv.get('name') or 'Emby'),
                    '_server': _esrv, '_title': _em_title, '_ids': _em_ids,
                    '_season': season, '_episode': episode,
                }, str(canonical_id)))
    except Exception:
        pass
    # Silo uses its official Jellyfin-compatible client surface, but remains a
    # separate source/account so it cannot overwrite or conflict with Emby.
    try:
        if api.silo_client.is_signed_in():
            _si_title = (meta or {}).get('name') or (meta or {}).get('title') or ''
            _si_ids = api.extract_ids(meta or {})
            _si_titles = []
            for _key in ('originalTitle', 'original_title', 'tvShowTitle',
                         'show_title', 'showname', 'show_name', 'seriesName',
                         'grandparentTitle', 'name', 'title', 'en_title',
                         'english_title', 'en_showname', 'ar_title', 'ar_showname'):
                _value = str((meta or {}).get(_key) or '').strip()
                if _value and _value.casefold() not in [x.casefold() for x in _si_titles]:
                    _si_titles.append(_value)
            if _si_title and _si_title.casefold() not in [x.casefold() for x in _si_titles]:
                _si_titles.insert(0, _si_title)
            # Remote title enrichment runs inside the native provider job.
            for _sisrv in api.silo_client.servers():
                native_targets.append(({
                    'id': '__silo__',
                    'name': 'Silo • %s' % (_sisrv.get('name') or 'Silo'),
                    '_server': _sisrv, '_title': _si_title, '_titles': _si_titles,
                    '_enrich_titles': True,
                    '_ids': _si_ids,
                    '_season': season, '_episode': episode,
                }, str(canonical_id)))
    except Exception as _silo_target_exc:
        api.xbmc.log('[DexHub] Silo source discovery failed: %s' % _silo_target_exc,
                 api.xbmc.LOGWARNING)
    # provider — one target per server, context carried on the provider dict.
    try:
        _plex_signed_in = api.plex_client.is_signed_in()
        _plex_enabled = api._get_bool_setting('plex_in_sources', True)
        if not _plex_signed_in:
            api.xbmc.log('[DexHub] Plex Native skipped: no account and no usable cached servers', api.xbmc.LOGWARNING)
        if _plex_signed_in and _plex_enabled:
            _pl_title = (meta or {}).get('name') or (meta or {}).get('title') or ''
            # Build the native Plex identifiers BEFORE any title enrichment.
            # Older builds referenced _pl_ids inside the TMDb title helpers
            # before assigning it; the surrounding broad try/except swallowed
            # the NameError and silently skipped every Plex server target.
            # Movies could appear through other providers, while Plex series
            # consistently returned nothing.
            _pl_ids = api.extract_ids(meta or {})
            _pl_titles = []
            for _key in ('originalTitle', 'original_title', 'tvShowTitle',
                         'show_title', 'showname', 'show_name', 'seriesName',
                         'grandparentTitle', 'name', 'title', 'en_title',
                         'english_title', 'en_showname', 'ar_title', 'ar_showname'):
                _value = str((meta or {}).get(_key) or '').strip()
                if _value and _value.casefold() not in [x.casefold() for x in _pl_titles]:
                    _pl_titles.append(_value)
            # Remote title enrichment runs inside the native provider job.

            # Continue Watching labels commonly arrive as
            # "Show Name — S01E09". Plex title search needs the show name.
            for _value in list(_pl_titles):
                _clean = api.re.sub(r'\s+(?:[—\-]\s*)?S\d{1,3}E\d{1,4}\s*$', '',
                                str(_value), flags=api.re.I).strip()
                if _clean and _clean.casefold() not in [x.casefold() for x in _pl_titles]:
                    _pl_titles.append(_clean)
            _plex_servers = api._plex_servers_budgeted()
            api.xbmc.log('[DexHub] Plex Native targets: servers=%d title=%s ids=%s' % (
                len(_plex_servers), _pl_title,
                ','.join('%s=%s' % (key, _pl_ids.get(key))
                         for key in ('imdb_id', 'tmdb_id', 'tvdb_id') if _pl_ids.get(key)) or 'none'),
                api.xbmc.LOGINFO)
            for _srv in _plex_servers:
                native_targets.append(({
                    'id': '__plex__',
                    'name': 'Plex • %s' % (_srv.get('name') or 'Plex'),
                    '_server': _srv, '_title': _pl_title, '_ids': _pl_ids,
                    '_titles': _pl_titles, '_year': (meta or {}).get('year') or '',
                    '_enrich_titles': True,
                    '_season': season, '_episode': episode,
                    '_native_item_id': (str((meta or {}).get('native_item_id') or '')
                                        if str((meta or {}).get('native_server_id') or '') == str(_srv.get('id') or '')
                                        else ''),
                }, str(canonical_id)))
        elif _plex_signed_in and not _plex_enabled:
            api.xbmc.log('[DexHub] Plex Native sources disabled in settings', api.xbmc.LOGINFO)
    except Exception as _plex_target_exc:
        api.xbmc.log('[DexHub] Plex Native target discovery failed: %s' % _plex_target_exc,
                 api.xbmc.LOGWARNING)
    try:
        api.xbmc.log('[DexHub] source targets: stremio=%d native=%d installed=%d media_type=%s canonical=%s' % (
            len(targets), len(native_targets), len(api.store.list_providers() or []),
            api._stremio_provider.normalized_media_type(media_type), canonical_id), api.xbmc.LOGINFO)
    except Exception:
        pass

    # Native servers are deliberately first.  Quick-to-results can stop the
    # initial scan as soon as Stremio returns enough rows; placing Plex/Emby at
    # the tail meant their tasks could still be queued and never reach the
    # visible session when another refresh worker was active.  Every native
    # server remains a separate target, so one offline server cannot cancel or
    # delay the results already returned by the other servers.
    try:
        native_targets = api._server_health.order_native_targets(native_targets)
    except Exception:
        pass
    # v5.10.20: keep Silo in the first native scheduling slot.  Health ranking
    # is still respected for every other native server, but Silo is a single
    # linked source and is cheap enough that putting it behind several Plex/
    # Emby servers made its chip intermittently arrive only after the picker
    # was already visible.  Interleaving below still starts one Stremio target
    # first, so this does not make initial search feel server-heavy.
    try:
        native_targets = sorted(
            list(native_targets),
            key=lambda t: 0 if str((t[0] or {}).get('id') or '') == '__silo__' else 1)
    except Exception:
        pass
    combined_targets = native_targets + targets
    try:
        # Keep connected servers in the first scheduling wave. Reordering the
        # combined list by old latency statistics could put Silo behind many
        # Stremio ID variants, so quick-open completed before the native task
        # even started and made a valid Silo match look absent.
        native_targets = api._provider_stats.order_targets(native_targets)
        targets = api._provider_stats.order_targets(targets)
        # v5.10.12: do not let native server probes monopolise the entire
        # first worker wave. After unified search, three connected servers can
        # occupy every 2-4 source worker while a fast Stremio aggregator waits
        # in the queue, making the picker feel heavy even though all work is
        # parallel. Start one Stremio job immediately, then interleave native
        # and Stremio jobs. Slow server lookups remain in the SAME live race
        # and continue filling the already-open results window.
        combined_targets = []
        _native_q = list(native_targets)
        _stremio_q = list(targets)
        if _stremio_q:
            combined_targets.append(_stremio_q.pop(0))
        while _native_q or _stremio_q:
            if _native_q:
                combined_targets.append(_native_q.pop(0))
            if _stremio_q:
                combined_targets.append(_stremio_q.pop(0))
        # A provider may contribute four id variants. Round-robin those jobs
        # after adaptive ranking so one addon cannot occupy every stream
        # worker while other providers (or another Plex/Emby server) wait.
        combined_targets = api._fair_search_job_order(combined_targets)
        api.xbmc.log('[DexHub] adaptive source order: %s' % ', '.join(
            str((p or {}).get('name') or (p or {}).get('id') or '?')
            for p, _rid in combined_targets[:8]), api.xbmc.LOGDEBUG)
    except Exception:
        pass
    return combined_targets
