# -*- coding: utf-8 -*-
"""Real-Debrid, AllDebrid, Premiumize and TorBox, from their public APIs.

Each service answers the same questions:
  device_start() / device_poll(state)   the QR sign-in (Premiumize: its API key)
  check(hashes)                         which torrents it has cached ({} when it cannot say)
  library()                             the account's own torrents (cached for 5 minutes)
  resolve(info_hash, want)              a playable link for the wanted file, or ''
A torrent Dex Hub added only to play it is removed again when it was not
ready; a torrent that was already in the account is never touched.
"""
import threading
import time
import urllib.parse

from . import accounts, files, net

try:
    import xbmc
except Exception:
    xbmc = None

APP = 'Dex Hub'
LIBRARY_KEEP = 300


def _wait(seconds):
    """False when Kodi is closing."""
    if xbmc is not None:
        return not xbmc.Monitor().waitForAbort(seconds)
    time.sleep(seconds)
    return True


def magnet(info_hash, name=''):
    link = 'magnet:?xt=urn:btih:%s' % info_hash
    if name:
        link += '&dn=%s' % urllib.parse.quote(str(name)[:200])
    return link


class NotReady(Exception):
    """The service does not have the torrent ready."""


class Service(object):
    id = ''
    name = ''
    auth = 'device'
    checks = False            # can say beforehand what is cached

    def __init__(self):
        self._library = (0, None)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- account
    def token(self):
        return accounts.token(self.id)

    def linked(self):
        return bool(self.token())

    def user(self):
        return ''

    # ---------------------------------------------------------------- library
    def library(self, fresh=False):
        with self._lock:
            stamp, rows = self._library
            if rows is not None and not fresh and time.time() - stamp < LIBRARY_KEEP:
                return rows
        try:
            rows = self._library_rows()
        except Exception as exc:
            accounts.log('%s library: %s' % (self.name, exc), True)
            rows = rows or []
        with self._lock:
            self._library = (time.time(), rows)
        return rows

    def forget_library(self):
        with self._lock:
            self._library = (0, None)

    def owned(self, info_hash):
        info_hash = str(info_hash or '').lower()
        for row in self.library():
            if str(row.get('hash') or '').lower() == info_hash:
                return row
        return None

    def _library_rows(self):
        return []

    def check(self, hashes):
        return {}


def _want(want):
    want = want or {}
    return dict(season=want.get('season'), episode=want.get('episode'), title=want.get('title') or '',
                year=want.get('year') or '', file_idx=want.get('file_idx'), filename=want.get('filename') or '')


# ==================================================================== Real-Debrid
class RealDebrid(Service):
    id, name = 'realdebrid', 'Real-Debrid'
    API = 'https://api.real-debrid.com/rest/1.0'
    OAUTH = 'https://api.real-debrid.com/oauth/v2'
    CLIENT = 'X245A4XAIBGVM'          # Real-Debrid's client id for open-source apps
    GRANT = 'http://oauth.net/grant_type/device/1.0'

    def device_start(self):
        data = net.get('%s/device/code?client_id=%s&new_credentials=yes' % (self.OAUTH, self.CLIENT))
        return {'code': data.get('user_code') or '', 'url': data.get('verification_url') or 'https://real-debrid.com/device',
                'qr': data.get('direct_verification_url') or data.get('verification_url') or 'https://real-debrid.com/device',
                'interval': int(data.get('interval') or 5), 'expires': int(data.get('expires_in') or 600),
                'state': {'device_code': data.get('device_code') or ''}}

    def device_poll(self, state):
        try:
            cred = net.get('%s/device/credentials?client_id=%s&code=%s' % (self.OAUTH, self.CLIENT,
                                                                            urllib.parse.quote(state['device_code'])))
        except net.HttpError as exc:
            if exc.code in (400, 403):
                return None              # not approved yet
            raise
        if not cred.get('client_id'):
            return None
        tok = net.post('%s/token' % self.OAUTH, form={'client_id': cred['client_id'], 'client_secret': cred['client_secret'],
                                                      'code': state['device_code'], 'grant_type': self.GRANT})
        return {'token': tok.get('access_token') or '', 'refresh': tok.get('refresh_token') or '',
                'client_id': cred['client_id'], 'client_secret': cred['client_secret'],
                'expires': int(time.time()) + int(tok.get('expires_in') or 3600) - 120}

    def _refresh(self):
        account = accounts.get(self.id)
        if not account.get('refresh'):
            return False
        try:
            tok = net.post('%s/token' % self.OAUTH, form={'client_id': account.get('client_id'),
                                                          'client_secret': account.get('client_secret'),
                                                          'code': account['refresh'], 'grant_type': self.GRANT})
        except net.HttpError as exc:
            if exc.code in (400, 401, 403):
                accounts.log('Real-Debrid refused the renewal: sign in again', True)
                accounts.remove(self.id)
            return False
        accounts.update(self.id, token=tok.get('access_token') or '', refresh=tok.get('refresh_token') or account['refresh'],
                        expires=int(time.time()) + int(tok.get('expires_in') or 3600) - 120)
        return bool(tok.get('access_token'))

    def _call(self, method, path, form=None, retry=True):
        account = accounts.get(self.id)
        if account.get('expires') and time.time() > float(account['expires']):
            self._refresh()
        try:
            return net.request(method, self.API + path, form=form,
                               headers={'Authorization': 'Bearer %s' % self.token()}, timeout=20)
        except net.HttpError as exc:
            if exc.code == 401 and retry and self._refresh():
                return self._call(method, path, form, retry=False)
            raise

    def user(self):
        return str(self._call('GET', '/user').get('username') or '')

    def _library_rows(self):
        rows = self._call('GET', '/torrents?limit=500') or []
        return [{'id': r.get('id'), 'hash': str(r.get('hash') or '').lower(), 'name': r.get('filename') or '',
                 'size': r.get('bytes') or 0, 'ready': r.get('status') == 'downloaded'} for r in rows if isinstance(r, dict)]

    def _link(self, info, chosen_id):
        selected = sorted([f for f in info.get('files') or [] if f.get('selected')], key=lambda f: f.get('id'))
        links = info.get('links') or []
        position = [f.get('id') for f in selected].index(chosen_id) if chosen_id in [f.get('id') for f in selected] else -1
        if position < 0 or position >= len(links):
            if len(links) == 1:
                position = 0
            else:
                return ''
        answer = self._call('POST', '/unrestrict/link', form={'link': links[position]})
        return str(answer.get('download') or '')

    def resolve(self, info_hash, want, wait=8):
        mine = self.owned(info_hash)
        torrent_id = mine.get('id') if mine else None
        added = False
        try:
            if not torrent_id:
                torrent_id = (self._call('POST', '/torrents/addMagnet', form={'magnet': magnet(info_hash)}) or {}).get('id')
                added = True
            if not torrent_id:
                return ''
            info = self._call('GET', '/torrents/info/%s' % torrent_id)
            pool = [{'name': f.get('path') or '', 'size': f.get('bytes') or 0, 'index': (f.get('id') or 1) - 1, 'id': f.get('id')}
                    for f in info.get('files') or []]
            if not added and info.get('status') == 'downloaded':
                pool = [p for p in pool if any(f.get('id') == p['id'] and f.get('selected') for f in info.get('files') or [])]
            chosen = files.pick(pool, **_want(want))
            if not chosen:
                raise NotReady('no fitting file')
            if info.get('status') == 'waiting_files_selection' or added:
                self._call('POST', '/torrents/selectFiles/%s' % torrent_id, form={'files': str(chosen['id'])})
            end = time.time() + wait
            while True:
                info = self._call('GET', '/torrents/info/%s' % torrent_id)
                status = info.get('status')
                if status == 'downloaded' and info.get('links'):
                    result = self._link(info, chosen['id'])
                    if result:
                        break
                    raise NotReady('no link')
                if status in ('magnet_error', 'error', 'virus', 'dead') or time.time() > end:
                    raise NotReady(status or 'not ready')
                if not _wait(1):
                    raise NotReady('stopped')
            if added:
                self._delete(torrent_id)            # the link stays good; the account stays as it was
            return result
        except NotReady as why:
            accounts.log('Real-Debrid: %s' % why)
            if added:
                self._delete(torrent_id)
            return ''

    def _delete(self, torrent_id):
        try:
            self._call('DELETE', '/torrents/delete/%s' % torrent_id)
        except Exception:
            pass


# ==================================================================== AllDebrid
class AllDebrid(Service):
    id, name = 'alldebrid', 'AllDebrid'
    API = 'https://api.alldebrid.com'
    AGENT = 'dexhub'

    def _call(self, path, form=None, params=None, auth=True):
        query = dict(params or {}, agent=self.AGENT)
        head = {'Authorization': 'Bearer %s' % self.token()} if auth else {}
        url = '%s/%s?%s' % (self.API, path, urllib.parse.urlencode(query, doseq=True))
        answer = net.request('POST' if form is not None else 'GET', url, form=form, headers=head, timeout=20)
        if (answer or {}).get('status') != 'success':
            code = ((answer or {}).get('error') or {}).get('code') or 'error'
            if code in ('AUTH_BAD_APIKEY', 'AUTH_USER_BANNED', 'AUTH_MISSING_APIKEY'):
                accounts.log('AllDebrid refused the key: sign in again', True)
            raise net.HttpError(code, str(answer)[:200], path)
        return answer.get('data') or {}

    def device_start(self):
        data = self._call('v4.1/pin/get', auth=False)
        return {'code': data.get('pin') or '', 'url': data.get('base_url') or 'https://alldebrid.com/pin/',
                'qr': data.get('user_url') or 'https://alldebrid.com/pin/', 'interval': 5,
                'expires': int(data.get('expires_in') or 600), 'state': {'pin': data.get('pin'), 'check': data.get('check')}}

    def device_poll(self, state):
        data = self._call('v4/pin/check', form={'pin': state['pin'], 'check': state['check']}, auth=False)
        if not data.get('activated') or not data.get('apikey'):
            return None
        return {'token': data['apikey']}

    def user(self):
        return str(((self._call('v4/user').get('user') or {}).get('username')) or '')

    def _library_rows(self):
        rows = self._call('v4.1/magnet/status', form={}).get('magnets') or []
        if isinstance(rows, dict):
            rows = list(rows.values())
        return [{'id': r.get('id'), 'hash': str(r.get('hash') or '').lower(), 'name': r.get('filename') or '',
                 'size': r.get('size') or 0, 'ready': r.get('statusCode') == 4} for r in rows if isinstance(r, dict)]

    @staticmethod
    def _flatten(nodes, path=''):
        for node in nodes or []:
            name = ('%s/%s' % (path, node.get('n') or '')).lstrip('/')
            if node.get('e'):
                for row in AllDebrid._flatten(node['e'], name):
                    yield row
            elif node.get('l'):
                yield {'name': name, 'size': node.get('s') or 0, 'link': node['l']}

    def resolve(self, info_hash, want, wait=8):
        mine = self.owned(info_hash)
        magnet_id = mine.get('id') if mine else None
        added = False
        try:
            if not magnet_id:
                row = (self._call('v4/magnet/upload', form={'magnets[]': magnet(info_hash)}).get('magnets') or [{}])[0]
                magnet_id = row.get('id')
                added = bool(magnet_id)
            if not magnet_id:
                return ''
            end = time.time() + wait
            while True:
                status = self._call('v4.1/magnet/status', form={'id': magnet_id}).get('magnets') or {}
                if isinstance(status, list):
                    status = status[0] if status else {}
                code = int(status.get('statusCode') or 0)
                if code == 4:
                    break
                if code > 4 or time.time() > end:
                    raise NotReady('status %s' % code)
                if not _wait(1):
                    raise NotReady('stopped')
            tree = (self._call('v4/magnet/files', form={'id[]': magnet_id}).get('magnets') or [{}])[0].get('files')
            pool = [dict(f, index=i) for i, f in enumerate(self._flatten(tree))]
            chosen = files.pick(pool, **_want(want))
            if not chosen:
                raise NotReady('no fitting file')
            link = str(self._call('v4/link/unlock', form={'link': chosen['link']}).get('link') or '')
            if not link:
                raise NotReady('no link')
            if added:
                self._delete(magnet_id)
            return link
        except (NotReady, net.HttpError) as why:
            accounts.log('AllDebrid: %s' % why)
            if added:
                self._delete(magnet_id)
            return ''

    def _delete(self, magnet_id):
        try:
            self._call('v4/magnet/delete', form={'id': magnet_id})
        except Exception:
            pass


# ==================================================================== Premiumize
class Premiumize(Service):
    id, name = 'premiumize', 'Premiumize'
    API = 'https://www.premiumize.me/api'
    auth = 'key'
    checks = True
    KEY_PAGE = 'https://www.premiumize.me/account'

    def _call(self, path, form=None, params=None, key=''):
        query = list((params or {}).items()) if isinstance(params, dict) else list(params or [])
        query.append(('apikey', key or self.token()))
        url = '%s/%s?%s' % (self.API, path, urllib.parse.urlencode(query, doseq=True))
        answer = net.request('POST' if form is not None else 'GET', url, form=form, timeout=25)
        if (answer or {}).get('status') == 'error':
            raise net.HttpError(answer.get('message') or 'error', '', path)
        return answer or {}

    def key_check(self, key):
        """The customer id when the key works, else ''."""
        answer = self._call('account/info', key=key)
        return str(answer.get('customer_id') or '') if answer.get('status') == 'success' else ''

    def user(self):
        return self.key_check(self.token())

    def check(self, hashes):
        out = {}
        hashes = list(hashes)
        for start in range(0, len(hashes), 100):
            part = hashes[start:start + 100]
            answer = self._call('cache/check', params=[('items[]', h) for h in part])
            for info_hash, flag in zip(part, answer.get('response') or []):
                out[info_hash.lower()] = 'cached' if flag else 'uncached'
        return out

    def _library_rows(self):
        rows = self._call('item/listall').get('files') or []
        return [{'id': r.get('id'), 'hash': '', 'name': r.get('path') or r.get('name') or '', 'size': r.get('size') or 0,
                 'ready': True, 'link': r.get('link') or r.get('stream_link') or ''} for r in rows if isinstance(r, dict)]

    def resolve(self, info_hash, want, wait=8):
        try:
            answer = self._call('transfer/directdl', form={'src': magnet(info_hash)})
        except net.HttpError as why:
            accounts.log('Premiumize: %s' % why)
            return ''
        pool = [{'name': f.get('path') or '', 'size': f.get('size') or 0, 'index': i,
                 'link': f.get('link') or f.get('stream_link') or ''} for i, f in enumerate(answer.get('content') or [])]
        chosen = files.pick(pool, **_want(want))
        return str((chosen or {}).get('link') or '')


# ==================================================================== TorBox
class TorBox(Service):
    id, name = 'torbox', 'TorBox'
    API = 'https://api.torbox.app/v1/api'
    SEARCH = 'https://search-api.torbox.app'
    checks = True

    def _head(self):
        return {'Authorization': 'Bearer %s' % self.token()}

    def _get(self, path, params=None, timeout=20):
        url = '%s/%s' % (self.API, path) + ('?' + urllib.parse.urlencode(params) if params else '')
        return (net.get(url, headers=self._head(), timeout=timeout) or {}).get('data')

    def device_start(self):
        data = net.get('%s/user/auth/device/start?app=%s' % (self.API, urllib.parse.quote(APP))).get('data') or {}
        return {'code': data.get('code') or '',
                'url': data.get('friendly_verification_url') or data.get('verification_url') or 'https://tor.box/link',
                'qr': data.get('verification_url') or data.get('friendly_verification_url') or 'https://tor.box/link',
                'interval': int(data.get('interval') or 5), 'expires': 600,
                'state': {'device_code': data.get('device_code') or ''}}

    def device_poll(self, state):
        try:
            answer = net.post('%s/user/auth/device/token' % self.API, json_body={'device_code': state['device_code']})
        except net.HttpError as exc:
            if exc.code in (400, 401, 403, 404, 425, 428):
                return None
            raise
        data = answer.get('data') if isinstance(answer, dict) else None
        if not answer.get('success') or not isinstance(data, dict) or not data.get('access_token'):
            return None
        return {'token': data['access_token']}

    def user(self):
        data = self._get('user/me') or {}
        return str(data.get('email') or data.get('customer') or '')

    def check(self, hashes):
        out = {}
        hashes = list(hashes)
        for start in range(0, len(hashes), 100):
            part = hashes[start:start + 100]
            for row in self._get('torrents/checkcached', {'hash': ','.join(part), 'format': 'list',
                                                          'list_files': 'false'}) or []:
                if isinstance(row, dict) and row.get('hash'):
                    out[str(row['hash']).lower()] = 'cached'
            for info_hash in part:
                out.setdefault(info_hash.lower(), 'uncached')
        return out

    def _library_rows(self):
        rows = self._get('torrents/mylist', {'limit': 1000, 'bypass_cache': 'true'}) or []
        return [{'id': r.get('id'), 'hash': str(r.get('hash') or '').lower(), 'name': r.get('name') or '',
                 'size': r.get('size') or 0, 'ready': bool(r.get('download_finished') or r.get('download_present')),
                 'files': r.get('files') or []} for r in rows if isinstance(r, dict)]

    def _create(self, kind, link, name, only_cached):
        if kind == 'usenet':
            path, fields, key = 'usenet/createusenetdownload', {'link': link, 'name': name or 'Dex Hub'}, 'usenetdownload_id'
        else:
            path, fields, key = 'torrents/createtorrent', {'magnet': link, 'seed': '3'}, 'torrent_id'
        if only_cached:
            fields['add_only_if_cached'] = 'true'
        answer = net.post('%s/%s' % (self.API, path), headers=self._head(), raw=net.multipart(fields), timeout=30)
        return ((answer or {}).get('data') or {}).get(key)

    def resolve(self, info_hash, want, wait=15, kind='torrents', link=''):
        area, id_name = ('usenet', 'usenet_id') if kind == 'usenet' else ('torrents', 'torrent_id')
        only_cached = not accounts.option('cache_and_play')
        try:
            mine = self.owned(info_hash) if kind != 'usenet' else None
            item_id = mine.get('id') if mine else self._create(kind, link or magnet(info_hash), (want or {}).get('name'),
                                                               only_cached)
            if not item_id:
                return ''
            end = time.time() + wait
            chosen = None
            while True:
                item = self._get('%s/mylist' % area, {'id': item_id, 'bypass_cache': 'true'}) or {}
                if isinstance(item, list):
                    item = item[0] if item else {}
                pool = [{'name': f.get('name') or f.get('short_name') or '', 'size': f.get('size') or 0,
                         'index': i, 'id': f.get('id')} for i, f in enumerate(item.get('files') or [])]
                if pool and (item.get('download_finished') or item.get('download_present') or item.get('cached')):
                    chosen = files.pick(pool, **_want(want))
                    break
                if time.time() > end or not _wait(1):
                    break
            if not chosen:
                return ''
            return str(self._get('%s/requestdl' % area, {'token': self.token(), id_name: item_id,
                                                        'file_id': chosen['id'], 'redirect': 'false'}) or '')
        except net.HttpError as why:
            accounts.log('TorBox: %s' % why)
            return ''

    def search(self, kind, ids, season=None, episode=None, timeout=20):
        """TorBox's own search (kind 'torrents' or 'usenet'), cached releases first."""
        ident = 'imdb:%s' % ids['imdb_id'] if ids.get('imdb_id') else 'tmdb:%s' % ids.get('tmdb_id')
        params = {'check_cache': 'true', 'metadata': 'false'}
        if season is not None and episode is not None:
            params.update(season=season, episode=episode)
        url = '%s/%s/%s?%s' % (self.SEARCH, kind, ident, urllib.parse.urlencode(params))
        data = (net.get(url, headers=self._head(), timeout=timeout) or {}).get('data') or {}
        return data.get('nzbs' if kind == 'usenet' else 'torrents') or []


SERVICES = {'realdebrid': RealDebrid(), 'alldebrid': AllDebrid(), 'premiumize': Premiumize(), 'torbox': TorBox()}


def service(name):
    return SERVICES.get(name)


def linked():
    return [SERVICES[s] for s in accounts.linked() if s in SERVICES]
