# -*- coding: utf-8 -*-
"""v5.10.451: Silo alerts on Telegram, by a Silo webhook (DexCloud hub 2.17).

Silo's maintainers asked for alerts to come as a personal webhook pushed by
the server, never by a client signing in to poll. Turning it on:

  1. the Silo server must allow personal webhooks (notifications/capabilities)
  2. the hub gives this TV a hook address (/api/device/silo-hook/start); the
     account's Telegram must be linked
  3. the webhook is made on the Silo server for this profile
     (POST /api/v2/notifications/webhooks, type generic) and its signing
     secret, which Silo returns once, goes to the hub
     (/api/device/silo-hook/secret), which keeps it encrypted
  4. Silo sends a test delivery: "Silo alerts reach Telegram"

Turning it off deletes the webhook on Silo (with its If-Match validator) and
the hook on the hub. The state (silo_tg_hook) belongs to this TV and this
Silo profile and never travels with the account sync.
"""
import json

import xbmcaddon
import xbmcgui

SETTING = 'silo_tg_hook'
NAME = 'Dex Hub · Telegram'


def _t(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _provider():
    from .providers import silo_provider
    return silo_provider


def _owner(auth):
    from .silo_notifications import owner
    return owner(auth)


def _state():
    try:
        value = json.loads(xbmcaddon.Addon('plugin.video.dexhub').getSetting(SETTING) or '{}')
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save(value):
    xbmcaddon.Addon('plugin.video.dexhub').setSetting(SETTING, json.dumps(value or {}))


def is_on():
    """On for the Silo profile signed in now (another profile's hook is not this one's)."""
    state = _state()
    auth = _provider().account() or {}
    return bool(state.get('webhook_id') and state.get('hub_id') and state.get('owner') == _owner(auth))


class Refused(Exception):
    pass


def enable():
    from . import hub_client
    provider = _provider()
    if not provider.is_signed_in():
        raise Refused(_t('سجّل دخول Silo أولًا', 'Sign in to Silo first'))
    if not hub_client.linked():
        raise Refused(_t('اربط هذا التلفزيون بـ DexCloud أولًا', 'Link this TV to DexCloud first'))
    auth = dict(provider.account() or {})
    caps = provider._api('notifications/capabilities', auth=auth) or {}
    hooks = caps.get('webhooks') or {}
    if not hooks.get('available') or 'generic' not in (hooks.get('supported_types') or ['generic']):
        raise Refused(_t('سيرفر Silo هذا ما فعّل فيه المدير الـ Webhooks الشخصية',
                         "This Silo server's admin has not turned on personal webhooks"))
    try:
        hook = hub_client.request('POST', '/api/device/silo-hook/start', {}, timeout=10)
    except hub_client.HubError as exc:
        if 'telegram' in str(exc).lower():
            raise Refused(_t('اربط تيليجرام بحسابك في DexCloud أولًا', 'Link Telegram to your DexCloud account first'))
        raise
    created = {}
    try:
        created = provider._api('notifications/webhooks', method='POST', auth=auth, data={
            'name': NAME, 'url': hook['url'], 'type': 'generic',
            'notify_watchlist': True, 'notify_favorites': True, 'notify_continue_watching': True,
            'notify_next_up': True, 'notify_requests': True}) or {}
        if not created.get('id') or not created.get('signing_secret'):
            raise Refused(_t('ما رجع Silo مفتاح التوقيع', 'Silo returned no signing secret'))
        host = ''
        try:
            from urllib.parse import urlsplit
            host = urlsplit(provider._base(auth.get('url'))).hostname or ''
        except Exception:
            pass
        hub_client.request('POST', '/api/device/silo-hook/secret', {
            'id': hook['id'], 'secret': created['signing_secret'], 'webhook_id': str(created['id']),
            'host': host}, timeout=10)
    except Exception:
        # nothing half made stays behind
        if created.get('id'):
            _delete_webhook(auth, str(created['id']))
        try:
            hub_client.request('POST', '/api/device/silo-hook/delete', {'id': hook['id']}, timeout=8)
        except Exception:
            pass
        raise
    _save({'hub_id': hook['id'], 'webhook_id': str(created['id']), 'owner': _owner(auth)})
    try:
        provider._api('notifications/webhooks/%s/test' % created['id'], method='POST', auth=auth)
    except Exception:
        pass        # the hook works without its test message
    return True


def _delete_webhook(auth, webhook_id):
    """Silo deletes a webhook only with its strong validator (If-Match)."""
    provider = _provider()
    listing = provider._api('notifications/webhooks', auth=auth) or {}
    row = next((r for r in listing.get('items') or [] if str(r.get('id')) == str(webhook_id)), None)
    if not row:
        return True                 # gone already
    url = '%s/api/v2/notifications/webhooks/%s' % (provider._base(auth.get('url')), webhook_id)
    provider._raw(url, method='DELETE', auth=auth, headers_extra={'If-Match': str(row.get('etag') or '')})
    return True


def disable():
    from . import hub_client
    state = _state()
    provider = _provider()
    auth = dict(provider.account() or {})
    error = None
    if state.get('webhook_id') and state.get('owner') == _owner(auth):
        try:
            _delete_webhook(auth, state['webhook_id'])
        except Exception as exc:
            error = exc
    if state.get('hub_id'):
        try:
            hub_client.request('POST', '/api/device/silo-hook/delete', {'id': state['hub_id']}, timeout=8)
        except Exception:
            pass
    _save({})
    if error:
        raise error
    return True


def toggle():
    """The settings row: turn it on or off, saying what happened."""
    try:
        if is_on():
            disable()
            xbmcgui.Dialog().notification('Silo', _t('وقفت تنبيهات Silo على تيليجرام', 'Silo alerts on Telegram are off'),
                                          xbmcgui.NOTIFICATION_INFO, 4000, False)
        else:
            enable()
            xbmcgui.Dialog().notification('Silo', _t('تنبيهات Silo صارت توصلك على تيليجرام', 'Silo alerts now reach your Telegram'),
                                          xbmcgui.NOTIFICATION_INFO, 5000, False)
    except Refused as exc:
        xbmcgui.Dialog().ok('Silo · Telegram', str(exc))
    except Exception as exc:
        xbmcgui.Dialog().notification('Silo', str(exc)[:120], xbmcgui.NOTIFICATION_ERROR, 5000, False)
