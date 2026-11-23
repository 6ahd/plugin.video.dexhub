# -*- coding: utf-8 -*-
"""My add-ons: the Stremio and Nuvio add-ons, clear and easy to manage (v5.10.606; the user,
23 Nov 2026: "removing sources and Stremio add-ons, I want it easy and clear").

Removing an add-on was only in the long-press menu of its row, which nothing pointed to. Here,
one screen lists every add-on with its picture, what it brings (streams, catalogs, subtitles,
metadata) and whether it is on. OK on one opens plain choices (open, turn off or on, refresh,
move, remove); "Remove several add-ons" removes many in one go. A removal always says what goes
and asks first.
"""
import xbmc
import xbmcgui


def _txt(ar, en):
    try:
        from .i18n import is_english
        return en if is_english() else ar
    except Exception:
        return ar


def _media(name):
    try:
        import os
        import xbmcaddon
        return os.path.join(xbmcaddon.Addon('plugin.video.dexhub').getAddonInfo('path'),
                            'resources', 'media', name)
    except Exception:
        return ''


def kinds(provider, keys=False):
    """What an add-on brings, in words: streams, catalogs, subtitles, metadata (the keys
    themselves with keys=True, for the phone page to word in its own language)."""
    manifest = (provider or {}).get('manifest') or {}
    names = set()
    for res in manifest.get('resources') or []:
        names.add(str(res.get('name') if isinstance(res, dict) else res or '').lower())
    if manifest.get('catalogs'):
        names.add('catalog')
    out = []
    for key, ar, en in (('stream', 'مصادر تشغيل', 'streams'), ('catalog', 'كتالوجات', 'catalogs'),
                        ('subtitles', 'ترجمة', 'subtitles'), ('meta', 'ميتاداتا', 'metadata'),
                        ('addon_catalog', 'قوائم إضافات', 'add-on lists')):
        if key in names:
            out.append(key if keys else _txt(ar, en))
    return out


def _host(provider):
    from urllib.parse import urlsplit
    url = (provider or {}).get('manifest_url') or (provider or {}).get('base_url') or ''
    return urlsplit(url).hostname or ''


def _logo(provider):
    manifest = (provider or {}).get('manifest') or {}
    return str(manifest.get('logo') or manifest.get('icon') or '')


def _choose(title, options, subtitle='', layout='list', preselect=-1):
    from .homeui.options import choose
    return choose(title, options, preselect=preselect, subtitle=subtitle, layout=layout)


def _home_changed():
    try:
        from .homeui.bar_editor import _home_changed as changed
        changed()
    except Exception as exc:
        xbmc.log('[DexHub] my add-ons: the Home was not rebuilt: %s' % exc, xbmc.LOGWARNING)


def _remove(provider_ids):
    from .dexhub import store
    gone = 0
    for provider_id in provider_ids:
        if not store.get_provider(provider_id):
            continue
        store.remove_provider(provider_id)
        gone += 1
        try:
            from . import index_render
            index_render.get_db().purge_provider(provider_id)
        except Exception as exc:
            xbmc.log('[DexHub] my add-ons: index of %s not purged: %s' % (provider_id, exc), xbmc.LOGWARNING)
    if gone:
        _home_changed()
    return gone


def _confirm_remove(names):
    if len(names) == 1:
        head = _txt('حذف «%s»؟', 'Remove "%s"?') % names[0]
    else:
        head = _txt('حذف %d إضافات؟', 'Remove %d add-ons?') % len(names) + '\n' + ' · '.join(names[:8]) + (' …' if len(names) > 8 else '')
    return xbmcgui.Dialog().yesno(_txt('حذف', 'Remove'), head + '\n\n' + _txt(
        'كتالوجاتها ومصادرها تختفي من Dex Hub. تقدر ترجعها بعدين من «إضافة مصدر».',
        'Their catalogs and streams leave Dex Hub. You can add them back later from "Add a source".'),
        nolabel=_txt('لا', 'No'), yeslabel=_txt('احذف', 'Remove'))


def _remove_many(rows):
    labels = []
    for row in rows:
        what = ' · '.join(kinds(row)) or _host(row)
        labels.append('%s  ·  %s' % (row.get('name') or row.get('id'), what))
    picked = xbmcgui.Dialog().multiselect(_txt('اختر الإضافات اللي تبي تحذفها', 'Pick the add-ons to remove'), labels)
    if not picked:
        return False
    chosen = [rows[i] for i in picked]
    if not _confirm_remove([r.get('name') or r.get('id') for r in chosen]):
        return False
    gone = _remove([r.get('id') for r in chosen])
    xbmcgui.Dialog().notification('Dex Hub', _txt('انحذفت %d إضافة', '%d add-ons removed') % gone,
                                  xbmcgui.NOTIFICATION_INFO, 3000)
    return True


def _move(row, rows):
    from .dexhub import store
    total = len(rows)
    at = next((i for i, r in enumerate(rows) if r.get('id') == row.get('id')), 0)
    text = xbmcgui.Dialog().numeric(0, _txt('المكان الجديد (1 إلى %d)', 'The new place (1 to %d)') % total, str(at + 1))
    try:
        to = max(1, min(total, int(text))) - 1
    except (TypeError, ValueError):
        return False
    if to == at:
        return False
    store.move_provider_to(row.get('id'), to)
    _home_changed()
    return True


def _one(api, row, rows):
    """The plain choices of one add-on. True when something changed."""
    from .dexhub import store
    name = row.get('name') or row.get('id')
    on = store.is_enabled(row)
    options = [('open', _txt('افتح كتالوجاتها', 'Open its catalogs')),
               ('toggle', _txt('أوقفها (تبقى محفوظة)', 'Turn it off (it stays saved)') if on else _txt('شغّلها', 'Turn it on')),
               ('refresh', _txt('حدّثها', 'Refresh it'))]
    if len(rows) > 1:
        options.append(('move', _txt('غيّر مكانها في الترتيب', 'Change its place in the order')))
    options.append(('remove', '[COLOR FFFF6B6B]%s[/COLOR]' % _txt('احذفها', 'Remove it')))
    what = ' · '.join(kinds(row))
    pick = _choose(name, [label for _k, label in options],
                   subtitle=' · '.join(x for x in ((_txt('شغالة', 'on') if on else _txt('موقوفة', 'off')), what, _host(row)) if x))
    if pick < 0:
        return False
    key = options[pick][0]
    if key == 'open':
        xbmc.executebuiltin('ActivateWindow(Videos,%s,return)' % api.build_url(action='provider_menu', provider_id=row.get('id')))
        return None
    if key == 'toggle':
        store.set_provider_enabled(row.get('id'), not on)
        _home_changed()
        xbmcgui.Dialog().notification('Dex Hub', (_txt('شغّلت: %s', 'On: %s') if not on else _txt('أوقفت: %s', 'Off: %s')) % name,
                                      xbmcgui.NOTIFICATION_INFO, 2500)
        return True
    if key == 'refresh':
        api.refresh_provider(row.get('id'))
        return True
    if key == 'move':
        return _move(row, rows)
    if key == 'remove' and _confirm_remove([name]):
        _remove([row.get('id')])
        xbmcgui.Dialog().notification('Dex Hub', _txt('انحذفت: %s', 'Removed: %s') % name, xbmcgui.NOTIFICATION_INFO, 2500)
        return True
    return False


def run(api):
    """The screen; it stays open until Back, so several changes are easy."""
    from .dexhub import store
    from .art import provider_art
    changed = False
    focus = 0
    while True:
        rows = store.list_providers() or []
        options = [(_txt('＋ إضافة مصدر', '+ Add a source'), '', _media('addons_plus.png'),
                    _txt('رابط إضافة Stremio أو Nuvio (manifest.json)', 'A Stremio or Nuvio add-on link (manifest.json)'))]
        if len(rows) > 1:
            options.append((_txt('حذف أكثر من وحدة', 'Remove several'), '', _media('menu/circle-xmark.png'),
                            _txt('اختر كم إضافة واحذفها مرة وحدة', 'Pick a few and remove them at once')))
        head = len(options)
        for row in rows:
            on = store.is_enabled(row)
            name = row.get('name') or row.get('id')
            try:
                icon = provider_art(name, row.get('manifest') or {}, base_url=row.get('base_url') or '').get('icon') or _logo(row)
            except Exception:
                icon = _logo(row)
            what = ' · '.join(kinds(row)) or _host(row)
            if not on:
                what = _txt('موقوفة', 'Off') + ' · ' + what
            options.append((name if on else '[COLOR FF8A8F98]%s[/COLOR]' % name,
                            _txt('شغالة', 'On') if on else _txt('موقوفة', 'Off'), icon, what))
        pick = _choose(_txt('إضافاتي', 'My add-ons'), options,
                       subtitle=_txt('%d إضافة · OK عليها: تشغيل، إيقاف، حذف', '%d add-ons · OK on one: on, off, remove') % len(rows),
                       layout='menu', preselect=focus)
        if pick < 0:
            break
        focus = pick
        if pick == 0:
            api.add_provider()
            changed = True
            continue
        if head == 2 and pick == 1:
            changed = _remove_many(rows) or changed
            focus = 0
            continue
        done = _one(api, rows[pick - head], rows)
        if done is None:
            return changed          # its catalogs opened in the Videos window
        changed = done or changed
    if changed:
        try:
            xbmc.executebuiltin('Container.Refresh')
        except Exception:
            pass
    return changed


def manage_one(api, provider_id):
    """One add-on's choices, from its own page; a removal goes back to the add-ons list."""
    from .dexhub import store
    row = store.get_provider(provider_id)
    if not row:
        return False
    done = _one(api, row, store.list_providers() or [])
    if done:
        if not store.get_provider(provider_id):
            xbmc.executebuiltin('Container.Update(%s,replace)' % api.build_url(action='providers'))
        else:
            xbmc.executebuiltin('Container.Refresh')
    return bool(done)
