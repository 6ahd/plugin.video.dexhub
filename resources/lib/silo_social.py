"""Native Silo watchlists, favorites and personal collection controls for Kodi."""
from urllib.parse import quote, urlencode


def _provider():
    from .providers import silo_provider
    return silo_provider


def _t(ar, en):
    from .i18n import is_english
    return en if is_english() else ar


def _owner(auth):
    from .silo_notifications import owner
    return owner(auth)


class Client:
    def __init__(self, auth=None):
        self.provider = _provider()
        self.auth = dict(auth or self.provider.account() or {})
        self.owner = _owner(self.auth)
        if not self.owner:
            raise self.provider.SiloError('اربط بروفايل Silo أولًا')
        self.major = self.provider.api_major(self.auth)

    def current(self):
        if self.owner != _owner(self.provider.account() or {}):
            raise self.provider.SiloError('تغير بروفايل Silo؛ أعد فتح القائمة')

    def api(self, path, **kw):
        self.current()
        value = self.provider._api(path, auth=self.auth, major=self.major, **kw)
        self.current()
        return value

    def membership(self, kind, item_id, included):
        if kind not in ('watchlist', 'favorites', 'watched'):
            raise ValueError('Invalid Silo membership kind')
        return self.api('%s/%s' % (kind, quote(str(item_id), safe='')),
                        method='PUT' if included else 'DELETE')

    def collections(self, item_id=''):
        params = {'contains_item': item_id} if item_id and self.major == 2 else None
        value = self.api('collections', params=params)
        rows = value.get('collections') or value.get('items') or []
        # Shared and smart/imported collections are displayable but not
        # mutable through a manual-membership action.
        return [r for r in rows if r.get('collection_type', 'manual') == 'manual' and
                (not r.get('creator_profile_id') or str(r['creator_profile_id']) == self.auth['profile_id'])]

    def create(self, name):
        name = str(name or '').strip()
        if not name:
            raise ValueError('اسم الكوليكشن مطلوب')
        return self.api('collections', method='POST', data={'name': name, 'collection_type': 'manual'})

    def collection_membership(self, collection, item_id, included):
        path = 'collections/%s/items/%s' % (quote(str(collection['id']), safe=''), quote(str(item_id), safe=''))
        return self.api(path, method='PUT' if included else 'DELETE',
                        data={'position': max(0, int(collection.get('item_count') or 0))} if included else None)

    def editor(self, collection):
        """Capture the entity tag before showing an edit/confirmation dialog."""
        self.current()
        path = 'collections/' + quote(str(collection['id']), safe='')
        url = '%s/api/v%s/%s' % (self.provider._base(self.auth['url']), self.major, path)
        payload, headers = self.provider._raw(url, auth=self.auth, return_headers=True)
        self.current()
        etag = next((v for k, v in headers.items() if k.lower() == 'etag'), '')
        if not etag or etag.startswith('W/') or not etag.startswith('"') or not etag.endswith('"'):
            raise self.provider.SiloError('السيرفر لا يوفر حماية تعديل الكوليكشن؛ حدّث Silo')
        detail = self.provider._decode(payload)
        if str(detail.get('id') or '') != str(collection['id']):
            raise self.provider.SiloError('رد الكوليكشن لا يطابق الطلب')
        return {'detail': detail, 'etag': etag, 'owner': self.owner}

    def edit(self, collection, name=None, delete=False, editor=None):
        """Apple's strong If-Match edit; do not overwrite another device's edit."""
        self.current()
        editor = editor or self.editor(collection)
        if editor['owner'] != self.owner or str(editor['detail'].get('id')) != str(collection['id']):
            raise self.provider.SiloError('تغيرت هوية الكوليكشن')
        etag = editor['etag']
        path = 'collections/' + quote(str(collection['id']), safe='')
        url = '%s/api/v%s/%s' % (self.provider._base(self.auth['url']), self.major, path)
        if not delete and not str(name or '').strip():
            raise ValueError('اسم الكوليكشن مطلوب')
        result = self.provider._raw(url, method='DELETE' if delete else 'PATCH', auth=self.auth,
                                    headers_extra={'If-Match': etag},
                                    data=None if delete else {'name': str(name).strip()})
        self.current()
        return result


def item_menu(item_id):
    import xbmcgui
    client = Client()
    raw = client.api('catalog/items/' + quote(str(item_id), safe=''))
    detail = raw.get('item') or raw.get('detail') or raw
    state = dict(detail.get('user_state') or {})
    for k, v in (detail.get('user_data') or {}).items():
        state.setdefault(k, v)
    rows = [(_t('إزالة من قائمة Silo' if state.get('in_watchlist') else 'أضف لقائمة Silo',
                'Remove from Silo watchlist' if state.get('in_watchlist') else 'Add to Silo watchlist'), 'watchlist', not state.get('in_watchlist')),
            (_t('إزالة من مفضلة Silo' if state.get('is_favorite') else 'أضف لمفضلة Silo',
                'Remove Silo favorite' if state.get('is_favorite') else 'Favorite in Silo'), 'favorites', not state.get('is_favorite')),
            (_t('اجعله غير مشاهد في Silo' if state.get('played') else 'اجعله مشاهدًا في Silo',
                'Mark unwatched in Silo' if state.get('played') else 'Mark watched in Silo'), 'watched', not state.get('played')),
            (_t('كوليكشنات Silo…', 'Silo collections…'), 'collection', True)]
    choice = xbmcgui.Dialog().contextmenu([r[0] for r in rows])
    if choice < 0:
        return False
    _, kind, included = rows[choice]
    if kind != 'collection':
        client.membership(kind, item_id, included)
        return True
    return collection_picker(client, item_id)


def collection_picker(client, item_id):
    import xbmcgui
    dialog = xbmcgui.Dialog()
    rows = client.collections(item_id)
    labels = [_t('＋ كوليكشن جديد', '＋ New collection')]
    labels += [('✓ ' if r.get('contains') else '') + str(r.get('name') or '') for r in rows]
    index = dialog.select(_t('كوليكشنات Silo', 'Silo collections'), labels)
    if index < 0:
        return False
    if index == 0:
        name = dialog.input(_t('اسم الكوليكشن', 'Collection name'))
        if not name.strip():
            return False
        collection = client.create(name)
        included = True
    else:
        collection = rows[index - 1]
        if 'contains' in collection:
            included = not bool(collection['contains'])
        else:
            action = dialog.contextmenu([_t('إضافة للعمل', 'Add title'), _t('إزالة العمل', 'Remove title')])
            if action < 0:
                return False
            included = action == 0
    client.collection_membership(collection, item_id, included)
    return True


def manage():
    import xbmcgui
    dialog = xbmcgui.Dialog()
    client = Client()
    rows = client.collections()
    choice = dialog.select(_t('إدارة كوليكشنات Silo', 'Manage Silo collections'),
                           [_t('＋ كوليكشن جديد', '＋ New collection')] + [str(r.get('name') or '') for r in rows])
    if choice < 0:
        return False
    if choice == 0:
        name = dialog.input(_t('اسم الكوليكشن', 'Collection name'))
        if name.strip():
            client.create(name)
            return True
        return False
    row = rows[choice - 1]
    editor = client.editor(row)
    row = editor['detail']
    action = dialog.contextmenu([_t('تغيير الاسم', 'Rename'), _t('حذف الكوليكشن', 'Delete collection')])
    if action == 0:
        name = dialog.input(_t('اسم الكوليكشن', 'Collection name'), defaultt=str(row.get('name') or ''))
        if name.strip():
            client.edit(row, name=name, editor=editor)
            return True
    elif action == 1 and dialog.yesno('Silo', _t('حذف الكوليكشن «%s»؟', 'Delete collection "%s"?') % row.get('name')):
        client.edit(row, delete=True, editor=editor)
        return True
    return False


def dispatch(item_id=''):
    import xbmc
    import xbmcgui
    try:
        changed = item_menu(item_id) if item_id else manage()
        if changed:
            # Existing list/section snapshots should be fetched again next
            # time; a social edit never triggers a full playback-history sync.
            from . import silo_notifications
            key = silo_notifications.owner(_provider().account() or {})
            if key:
                silo_notifications._update(key, lambda state: state.update(refresh_after=0))
            try:
                from . import watch_sources
                with watch_sources._LOCK:
                    snapshot = watch_sources.snapshot()
                    snapshot['t'] = 0
                    watch_sources._write('watch_sources_cache.json', snapshot)
                watch_sources._changed()
            except Exception:
                pass
            from . import progress_events
            progress_events.changed()
            xbmc.executebuiltin('Container.Refresh')
            xbmcgui.Dialog().notification('Silo', _t('حُفظ التغيير', 'Saved'))
    except Exception as exc:
        xbmcgui.Dialog().notification('Silo', str(exc), xbmcgui.NOTIFICATION_ERROR)
