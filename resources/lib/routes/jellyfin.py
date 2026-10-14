"""Lazy Jellyfin browser; media work stays in the shared Emby client."""
import time
from ..providers import jellyfin_provider as client
from ..server_art import listing_content


def _servers_changed():
    """v5.10.198: the pages that list servers read them again at once."""
    try:
        from .. import server_accounts
        server_accounts.changed('jellyfin')
    except Exception:
        pass


def _newest(library_type):
    from ..sort_prefs import newest_first
    return newest_first('jellyfin', library_type)


def run(api, action, params):
    from .simple_entry import _txt
    dialog = api.xbmcgui.Dialog()
    if action == 'jellyfin_login':
        from .. import kb_private
        # v5.10.110: account data: no keyboard suggestions (kb_private)
        url = kb_private.dialog_input(_txt('عنوان سيرفر Jellyfin', 'Jellyfin server URL'))
        if not url:
            return
        method = dialog.select('Jellyfin', ['Quick Connect', _txt('اسم المستخدم وكلمة المرور', 'Username and password')])
        if method < 0:
            return
        try:
            if method == 1:
                username = kb_private.dialog_input(_txt('اسم المستخدم', 'Username'))
                if not username:
                    return
                password = dialog.input(_txt('كلمة المرور', 'Password'), option=api.xbmcgui.ALPHANUM_HIDE_INPUT)
                client.sign_in(url, username, password)
            else:
                pairing = client.quick_start(url)
                progress = api.xbmcgui.DialogProgress()
                progress.create('Jellyfin • Quick Connect', _txt(
                    'أدخل هذا الكود في حساب Jellyfin من خيار Quick Connect: ',
                    'Enter this code in your signed-in Jellyfin account under Quick Connect: ') + str(pairing['Code']))
                monitor = api.xbmc.Monitor()
                deadline = time.monotonic() + 180
                linked = False
                try:
                    while not progress.iscanceled() and not monitor.abortRequested() and time.monotonic() < deadline:
                        if client.quick_poll(pairing):
                            linked = True
                            break
                        if monitor.waitForAbort(2):
                            break
                finally:
                    progress.close()
                if not linked:
                    dialog.notification('Jellyfin', _txt('انتهى الربط أو تم إلغاؤه', 'Pairing expired or cancelled'))
                    return
            _servers_changed()
            api.xbmc.executebuiltin('Container.Refresh')
        except Exception:
            # Never expose a Quick Connect secret or auth URL in error text.
            dialog.ok('Jellyfin', _txt('تعذر الربط. تحقق من العنوان وتفعيل Quick Connect على السيرفر.',
                                     'Could not connect. Check the URL and server Quick Connect setting.'))
        return
    if action == 'jellyfin_logout':
        client.sign_out()
        _servers_changed()
        api.xbmc.executebuiltin('Container.Refresh')
        return
    servers = client.servers()
    if not servers:
        api.add_item(_txt('ربط Jellyfin', 'Connect Jellyfin'), api.build_url(action='jellyfin_login'), is_folder=False)
        return api.end_dir(cache=False)
    server = servers[0]
    key = params.get('key') or ''
    if action == 'jellyfin_play':
        return api.emby_play(key, server=server, backend='jellyfin', ui_seed_key=params.get('ui_seed_key') or '')
    content = 'videos'
    try:
        if action == 'jellyfin_menu':
            api.add_item(_txt('بحث Jellyfin', 'Search Jellyfin'), api.build_url(action='jellyfin_search'))
            api.add_item(_txt('متابعة المشاهدة', 'Continue Watching'), api.build_url(action='jellyfin_continue'))
            for library in client.libraries(server):
                api.add_item('%s • %s' % (library['title'], server['name']), api.build_url(
                    action='jellyfin_library', key=library['key'], title=library['title'], library_type=library['type']), art=library.get('artwork') or api.root_art('jellyfin'))
            api.add_item(_txt('فصل Jellyfin', 'Disconnect Jellyfin'), api.build_url(action='jellyfin_logout'), is_folder=False)
            return api.end_dir(content='files', cache=False)
        start = int(params.get('start') or 0)
        if action == 'jellyfin_search':
            query = params.get('query') or dialog.input(_txt('بحث Jellyfin', 'Search Jellyfin'))
            rows = client.search_server(server, query) if query else []
            total = len(rows)
        elif action == 'jellyfin_continue':
            rows, total = client.resume(server, start=start)
            if not start:
                # v5.10.198: the next episode of each series in progress, as
                # Emby's Continue Watching has it (Plex's On Deck too)
                try:
                    seen = set(r.get('rating_key') for r in rows)
                    for row in client.next_up(server):
                        if row.get('rating_key') not in seen:
                            rows.append(row)
                            seen.add(row.get('rating_key'))
                    total = max(total, len(rows))
                except Exception:
                    pass
        else:
            library = action == 'jellyfin_library'
            # v5.10.106: a library page takes the Home's sort and filters
            # (the same names as Emby's library page)
            rows, total = client.children(server, key, start=start, recursive=library,
                include_types=('Series' if params.get('library_type') == 'show' else 'Movie') if library else '',
                # v5.10.198: by default, what was added last (sort_prefs.newest_first)
                sort=(params.get('sort') or _newest(params.get('library_type'))) if library else '',
                filters=api.emby_library_filters(
                    params.get('genre', ''), params.get('unwatched', ''),
                    params.get('decade', ''), params.get('rating', '')) if library else None)
        content = listing_content(rows, ('tvshows' if params.get('library_type') == 'show' else 'movies')
                                  if action == 'jellyfin_library' else params.get('content') or 'videos')
        for item in rows:
            kind = item.get('media_type')
            folder = kind in ('show', 'season')
            if action == 'jellyfin_library':
                item['library_name'] = params.get('title') or ''
            api.add_item(item.get('title') or 'Jellyfin', api.build_url(
                action='jellyfin_children' if folder else 'jellyfin_play', key=item['rating_key'],
                content='seasons' if kind == 'show' else 'episodes' if kind == 'season' else ''),
                is_folder=folder, info=api._plex_item_info(item), ids=api._plex_item_ids(item),
                art=api._emby_art(item, server), label2=api._native_library_facts(item, 'Jellyfin'))
        if rows and start + len(rows) < total and action != 'jellyfin_search':
            extra = dict(params, action=action, start=str(start + len(rows)))
            api.add_item(_txt('المزيد', 'More'), api.build_url(**extra))
    except Exception:
        dialog.notification('Jellyfin', _txt('تعذر تحميل العناصر', 'Could not load items'))
    return api.end_dir(content=content, cache=False)
