# -*- coding: utf-8 -*-
"""The Nuvio profile on skin.dexhub's Home (v5.10.114).

The add-on's Home shows the profile in use behind its logo and switches it
there (homeui/profile.py); skin.dexhub shows the profile's picture and name
in the same place (Home properties dhs.profile.*) and its button runs
skin_profile, the same switch: pick a profile, pull its Home, progress and
TMDb settings from Nuvio, then the skin's rows are worked out again.
"""
import threading

import xbmc
import xbmcgui

from . import common as C

PROP_NAME = 'dhs.profile.name'
PROP_AVATAR = 'dhs.profile.avatar'
PROP_LINKED = 'dhs.profile.linked'


def _nuvio():
    from ..dexhub.nuvio_stremio_sync import Nuvio
    return Nuvio


def _row_index(row):
    try:
        return int(row.get('profile_index', row.get('profileIndex', row.get('index'))))
    except Exception:
        return None


def publish(fetch=False):
    """Name and picture of the profile in use onto the Home; ``fetch``: a
    profile chosen before Dex Hub kept pictures gets its picture from Nuvio
    (on a thread)."""
    try:
        nuvio = _nuvio()
        linked = bool(nuvio.is_linked())
        token = nuvio.token() if linked else {}
    except Exception:
        linked, token = False, {}
    C.set_prop(PROP_LINKED, '1' if linked else '')
    C.set_prop(PROP_NAME, str(token.get('profile_name') or '') if linked else '')
    C.set_prop(PROP_AVATAR, str(token.get('profile_avatar') or '') if linked else '')
    if fetch and linked and token.get('profile_name') and 'profile_avatar' not in token:
        threading.Thread(target=_fetch_avatar, name='dexhub-skin-avatar').start()


def _fetch_avatar():
    try:
        nuvio = _nuvio()
        token = nuvio.token()
        try:
            current = int(token.get('profile_index'))
        except Exception:
            return
        for row in nuvio.profiles() or []:
            if _row_index(row) == current:
                avatar = str(row.get('avatar_url') or row.get('avatar') or row.get('image') or '')
                token = nuvio.token()
                token['profile_avatar'] = avatar if avatar.startswith('http') else ''
                nuvio.save_token(token)
                C.set_prop(PROP_AVATAR, token['profile_avatar'])
                return
    except Exception as exc:
        C.log('profile picture: %s' % exc, xbmc.LOGDEBUG)


def switch(params, handle):
    """The profile button of the skin's Home (homeui window._profile_click)."""
    from . import rows
    from ..homeui import profile as P
    app = rows.app()
    if not P.linked():
        if xbmcgui.Dialog().yesno('Dex Hub', app.tr('لا يوجد حساب Nuvio مربوط. تربطه الآن؟')):
            xbmc.executebuiltin('RunPlugin(%s)' % C.url('nuvio_connect'))
        return None
    try:
        name = P.choose(app.tr)
    except Exception:
        import traceback
        C.log('profile switch failed:\n%s' % traceback.format_exc(), xbmc.LOGWARNING)
        name = ''
    if not name:
        return None
    publish()
    C.log('Nuvio profile switched from the skin')
    bar = xbmcgui.DialogProgressBG()
    bar.create('Dex Hub', app.tr('تحميل بروفايل %s…') % name)
    try:
        import xbmcaddon
        failed = P.pull(xbmcaddon.Addon(C.ADDON_ID), app.log)
    finally:
        bar.close()
    try:
        app._tmdb_settings = None
    except Exception:
        pass
    # the add-on's own Home reads the new profile's rows again too
    import os
    for page in ('all', 'movie', 'series'):
        try:
            os.remove(os.path.join(app.profile, 'home_%s.json' % page))
        except Exception:
            pass
    if failed:
        xbmcgui.Dialog().notification('Dex Hub', app.tr('تعذر تحديث بعض بيانات البروفايل: %s') % ', '.join(failed),
                                      xbmcgui.NOTIFICATION_WARNING, 4000)
    # the skin's rows: worked out again for this profile and published
    try:
        rows.publish(reason='profile')
    except Exception:
        C.ask_republish('profile')
    return None
