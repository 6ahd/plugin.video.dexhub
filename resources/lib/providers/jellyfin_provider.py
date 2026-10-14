"""Jellyfin uses the shared media client but its own account/device identity."""
import time
from .. import emby_client as shared


def account():
    return shared.account('jellyfin')


def is_signed_in():
    return shared.is_signed_in('jellyfin')


def servers():
    return shared.servers('jellyfin')


def sign_out():
    return shared.sign_out('jellyfin')


def _name(auth):
    try:
        info = shared._api(dict(auth, backend='jellyfin'), '/System/Info/Public')
        auth['server_name'] = info.get('ServerName') or 'Jellyfin'
    except Exception:
        auth['server_name'] = 'Jellyfin'
    shared._save_json_setting('jellyfin_auth_json', auth)
    return auth


def sign_in(url, username, password=''):
    return _name(shared.sign_in(url, username, password, backend='jellyfin'))


def quick_start(url):
    server = {'url': shared._base({'url': url}), 'backend': 'jellyfin'}
    if not server['url']:
        raise ValueError('Server URL required')
    # The secret is never shown or logged; only Code is displayed to the user.
    value = shared._api(server, '/QuickConnect/Initiate', method='POST')
    if not value.get('Secret') or not value.get('Code'):
        raise ValueError('Quick Connect unavailable')
    return dict(value, server=server)


def quick_poll(pairing):
    server = pairing['server']
    secret = pairing['Secret']
    state = shared._api(server, '/QuickConnect/Connect', params={'secret': secret})
    if not state.get('Authenticated'):
        return False
    value = shared._api(server, '/Users/AuthenticateWithQuickConnect',
                        method='POST', data={'Secret': secret})
    user = value.get('User') or {}
    if not value.get('AccessToken') or not user.get('Id'):
        raise ValueError('Invalid Quick Connect response')
    _name({'url': server['url'], 'backend': 'jellyfin',
           'token': value['AccessToken'], 'user_id': user['Id'],
           'username': user.get('Name') or '', 'signed_in_at': int(time.time())})
    return True


def __getattr__(name):
    return getattr(shared, name)
